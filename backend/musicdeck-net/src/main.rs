//! MusicDeck's internet link between paired devices.
//!
//! One iroh endpoint (QUIC: encrypted end to end, each side proven by its key; hole punching, and n0's
//! public relays when a direct path can't be made; found by n0's DNS and by the Mainline DHT). Python
//! (musicdeck/net.py) drives it with JSON lines on stdin and hears from it on stdout:
//!
//!   in:  {"op":"allow","nodes":[id...]}             the paired devices; nobody else is answered or dialled
//!        {"op":"call","id":n,"node":id,"req":{...}}  a request to a paired device -> "result"
//!        {"op":"reply","rid":n,"res":...}            the answer to a "request" (or "error": text)
//!        {"op":"stats"}                              -> "stats": bytes sent and received, per device
//!   out: {"op":"ready","node":id}                    this device's id (its public key)
//!        {"op":"peer","node":id,"online":bool,"via":"direct"|"relay"|null}
//!        {"op":"result","id":n,"res":...} or {"op":"result","id":n,"error":text}
//!        {"op":"request","rid":n,"node":id,"req":{...}}  a paired device asks (already checked here)
//!        {"op":"log","msg":text}
//!
//! A request is {"method":"GET"|"POST","path":"/p/...","body":...}, and only the paths in ALLOWED: no
//! Local files, discs or anything else of the backend crosses the internet. No port mapping
//! (UPnP, NAT-PMP, PCP) is ever asked of the router.

use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use anyhow::{bail, Context, Result};
use iroh::endpoint::{presets, Connection, PortmapperConfig, QuicTransportConfig};
use iroh::{Endpoint, EndpointAddr, EndpointId, SecretKey};
use iroh_mainline_address_lookup::DhtAddressLookup;
use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::sync::{mpsc, oneshot, Notify};

const ALPN: &[u8] = b"musicdeck/1";
/// What a paired device may ask, over the internet (the query string aside).
const ALLOWED: &[(&str, &str)] = &[
    ("GET", "/p/hello"),
    ("GET", "/p/session"),
    ("GET", "/p/plays"),
    ("GET", "/p/playlists"),
    ("POST", "/p/playlists/changed"),
    // "Continue here": the other device pauses (only ever asked by a person, on the device taking over)
    ("POST", "/p/took-over"),
    // it played, paused or changed song (the other's Your devices shows it at once)
    ("POST", "/p/session/changed"),
    // played on as a speaker by the other (its streaming services' songs: Local files and discs stay home)
    ("POST", "/p/cast/start"),
    ("POST", "/p/cast/load"),
    ("POST", "/p/cast/control"),
    ("POST", "/p/cast/end"),
    ("GET", "/p/cast/status"),
    ("POST", "/p/unpair"),
];
const MAX_REQUEST: usize = 16 * 1024;
const MAX_ANSWER: usize = 4 * 1024 * 1024;
/// Messages bigger than this are deflated (playlists, pages of plays); smaller ones aren't worth it.
const COMPRESS_FROM: usize = 1024;
const KEEP_ALIVE: Duration = Duration::from_secs(10);
/// A device that hasn't been heard from in this long is offline (QUIC alone can take a minute: it keeps
/// trying other paths).
const SILENT: Duration = Duration::from_secs(25);
const IDLE: Duration = Duration::from_secs(60);
const DIAL_TIMEOUT: Duration = Duration::from_secs(20);
const ANSWER_TIMEOUT: Duration = Duration::from_secs(20);

type Out = mpsc::UnboundedSender<Value>;

#[derive(Default)]
struct Peer {
    conn: Option<Connection>,
    wake: Arc<Notify>,
    stop: Arc<Notify>,
}

struct State {
    out: Out,
    allowed: Mutex<HashMap<EndpointId, Peer>>,
    asked: Mutex<HashMap<u64, oneshot::Sender<Value>>>,
    next_rid: AtomicU64,
    /// bytes of connections already closed, per device (open ones are read from their stats)
    spent: Mutex<HashMap<EndpointId, (u64, u64)>>,
}

impl State {
    fn say(&self, v: Value) {
        let _ = self.out.send(v);
    }
    fn log(&self, msg: impl Into<String>) {
        self.say(json!({"op": "log", "msg": msg.into()}));
    }
    fn is_allowed(&self, id: &EndpointId) -> bool {
        self.allowed.lock().unwrap().contains_key(id)
    }
    fn conn(&self, id: &EndpointId) -> Option<Connection> {
        self.allowed.lock().unwrap().get(id).and_then(|p| p.conn.clone())
    }
    fn count(&self, id: EndpointId, conn: &Connection) {
        let s = conn.stats();
        let mut spent = self.spent.lock().unwrap();
        let e = spent.entry(id).or_default();
        e.0 += s.udp_tx.bytes;
        e.1 += s.udp_rx.bytes;
    }
}

// --- messages on a stream: one byte (0 plain, 1 deflated) and JSON ---

fn pack(v: &Value) -> Vec<u8> {
    let raw = serde_json::to_vec(v).unwrap_or_default();
    if raw.len() >= COMPRESS_FROM {
        let packed = miniz_oxide::deflate::compress_to_vec(&raw, 6);
        if packed.len() < raw.len() {
            let mut out = Vec::with_capacity(packed.len() + 1);
            out.push(1);
            out.extend_from_slice(&packed);
            return out;
        }
    }
    let mut out = Vec::with_capacity(raw.len() + 1);
    out.push(0);
    out.extend_from_slice(&raw);
    out
}

fn unpack(data: &[u8], limit: usize) -> Result<Value> {
    let (kind, rest) = data.split_first().context("empty message")?;
    let raw = match kind {
        0 => rest.to_vec(),
        1 => miniz_oxide::inflate::decompress_to_vec_with_limit(rest, limit)
            .map_err(|_| anyhow::anyhow!("bad or oversized deflate"))?,
        _ => bail!("unknown message kind"),
    };
    if raw.len() > limit {
        bail!("message too big");
    }
    Ok(serde_json::from_slice(&raw)?)
}

/// A request a paired device may make: {"method","path"[,"body"]} with nothing else, an allowed path,
/// a short printable query, a body only on POST.
fn valid_request(req: &Value) -> Option<Value> {
    let obj = req.as_object()?;
    if obj.keys().any(|k| !matches!(k.as_str(), "method" | "path" | "body")) {
        return None;
    }
    let method = obj.get("method")?.as_str()?;
    let path = obj.get("path")?.as_str()?;
    if path.len() > 200 || !path.bytes().all(|b| b.is_ascii_graphic()) {
        return None;
    }
    let (base, query) = path.split_once('?').unwrap_or((path, ""));
    if !ALLOWED.iter().any(|(m, p)| *m == method && *p == base) {
        return None;
    }
    if !query.is_empty() && !query.bytes().all(|b| b.is_ascii_alphanumeric() || b"=&._-".contains(&b)) {
        return None;
    }
    let body = obj.get("body").cloned().unwrap_or(Value::Null);
    if method == "GET" && !body.is_null() {
        return None;
    }
    Some(json!({"method": method, "path": path, "body": body}))
}

// --- answering ---

async fn serve(state: Arc<State>, conn: Connection) {
    let node = conn.remote_id();
    loop {
        let (mut send, mut recv) = match conn.accept_bi().await {
            Ok(s) => s,
            Err(_) => break,
        };
        let state = state.clone();
        tokio::spawn(async move {
            let answer = match recv.read_to_end(MAX_REQUEST).await {
                Ok(data) => match unpack(&data, MAX_REQUEST).ok() {
                    // a device that just connected checks it's welcome here: answered without Python
                    Some(req) if req.get("ping").is_some() => json!({"res": "pong"}),
                    req => match req.as_ref().and_then(valid_request) {
                        Some(req) => ask_python(&state, node, req).await,
                        None => json!({"error": "not allowed"}),
                    },
                },
                Err(_) => json!({"error": "too big"}),
            };
            let _ = send.write_all(&pack(&answer)).await;
            let _ = send.finish();
        });
    }
}

async fn ask_python(state: &State, node: EndpointId, req: Value) -> Value {
    let rid = state.next_rid.fetch_add(1, Ordering::Relaxed);
    let (tx, rx) = oneshot::channel();
    state.asked.lock().unwrap().insert(rid, tx);
    state.say(json!({"op": "request", "rid": rid, "node": node.to_string(), "req": req}));
    match tokio::time::timeout(ANSWER_TIMEOUT, rx).await {
        Ok(Ok(answer)) => answer,
        _ => {
            state.asked.lock().unwrap().remove(&rid);
            json!({"error": "no answer"})
        }
    }
}

async fn accepting(state: Arc<State>, ep: Endpoint) {
    let me = ep.id();
    while let Some(incoming) = ep.accept().await {
        let state = state.clone();
        tokio::spawn(async move {
            let Ok(accepting) = incoming.accept() else { return };
            let Ok(conn) = accepting.await else { return };
            let node = conn.remote_id();
            if conn.alpn() != ALPN || !state.is_allowed(&node) {
                conn.close(403u32.into(), b"not paired");
                return;
            }
            // one connection a pair: this one is kept when this side has none, or when both dialled at
            // once (the one the lower id dialled wins, on both sides)
            let adopted = {
                let mut allowed = state.allowed.lock().unwrap();
                match allowed.get_mut(&node) {
                    Some(p) if p.conn.is_none() || node < me => {
                        if let Some(old) = p.conn.replace(conn.clone()) {
                            old.close(0u32.into(), b"duplicate");
                        }
                        p.wake.notify_one();
                        true
                    }
                    _ => false,
                }
            };
            if adopted {
                tokio::spawn(serve(state.clone(), conn.clone()));
                watch(&state, node, &conn).await;
            } else {
                serve(state.clone(), conn.clone()).await;
                state.count(node, &conn);
            }
        });
    }
}

// --- keeping a connection to each paired device (its presence) ---

fn via(conn: &Connection) -> &'static str {
    let paths = conn.paths();
    let selected = paths.iter().find(|p| p.is_selected());
    match selected {
        Some(p) if p.is_relay() => "relay",
        Some(_) => "direct",
        None => "relay",
    }
}

/// Shows a device online while its connection lives and it's heard from; offline after.
async fn watch(state: &State, node: EndpointId, conn: &Connection) {
    let mut shown = via(conn);
    state.say(json!({"op": "peer", "node": node.to_string(), "online": true, "via": shown}));
    let (mut heard, mut heard_at) = (conn.stats().udp_rx.bytes, tokio::time::Instant::now());
    loop {
        tokio::select! {
            _ = conn.closed() => break,
            _ = tokio::time::sleep(Duration::from_secs(3)) => {
                let rx = conn.stats().udp_rx.bytes;
                if rx != heard {
                    (heard, heard_at) = (rx, tokio::time::Instant::now());
                } else if heard_at.elapsed() > SILENT {
                    conn.close(0u32.into(), b"silent");
                    break;
                }
                let now = via(conn);
                if now != shown {
                    shown = now;
                    state.say(json!({"op": "peer", "node": node.to_string(), "online": true, "via": shown}));
                }
            }
        }
    }
    state.count(node, conn);
    let mut allowed = state.allowed.lock().unwrap();
    if let Some(p) = allowed.get_mut(&node) {
        if p.conn.as_ref().is_some_and(|c| c.stable_id() == conn.stable_id()) {
            p.conn = None;
            state.say(json!({"op": "peer", "node": node.to_string(), "online": false, "via": null}));
        }
    }
}

async fn ping(conn: &Connection) -> bool {
    let asked = async {
        let (mut send, mut recv) = conn.open_bi().await.ok()?;
        send.write_all(&pack(&json!({"ping": 1}))).await.ok()?;
        send.finish().ok()?;
        let data = recv.read_to_end(1024).await.ok()?;
        unpack(&data, 1024).ok()?.get("res").cloned()
    };
    matches!(tokio::time::timeout(DIAL_TIMEOUT, asked).await, Ok(Some(_)))
}

/// Keeps a connection to a paired device: dialled from here, unless it dialled this one first.
async fn keep_up(state: Arc<State>, ep: Endpoint, node: EndpointId, wake: Arc<Notify>, stop: Arc<Notify>) {
    // both sides start at once: the one with the higher id waits a moment, so usually only one dials
    let mut wait = if ep.id() > node { Duration::from_secs(3) } else { Duration::ZERO };
    loop {
        tokio::select! {
            _ = stop.notified() => return,
            _ = wake.notified() => {}
            _ = tokio::time::sleep(wait) => {}
        }
        // a connection it made (adopted in accepting): wait for it to end
        while let Some(conn) = state.conn(&node) {
            tokio::select! {
                _ = stop.notified() => { conn.close(0u32.into(), b"unpaired"); return; }
                _ = conn.closed() => {}
            }
            tokio::time::sleep(Duration::from_millis(300)).await;
        }
        if !state.is_allowed(&node) {
            return;
        }
        let dial = tokio::time::timeout(DIAL_TIMEOUT, ep.connect(EndpointAddr::from(node), ALPN));
        let conn = tokio::select! {
            _ = stop.notified() => return,
            r = dial => match r { Ok(Ok(c)) => Some(c), _ => None },
        };
        // connected, and welcome there: it answers (a device that hasn't been told this one is paired refuses)
        let conn = match conn {
            Some(conn) if ping(&conn).await => Some(conn),
            Some(conn) => {
                conn.close(0u32.into(), b"refused");
                None
            }
            None => None,
        };
        let Some(conn) = conn else {
            wait = (wait * 2).clamp(Duration::from_secs(5), Duration::from_secs(60));
            continue;
        };
        wait = Duration::from_secs(5);
        {
            let mut allowed = state.allowed.lock().unwrap();
            match allowed.get_mut(&node) {
                // it dialled this one meanwhile: the one the lower id dialled is kept
                Some(p) if p.conn.is_some() && ep.id() > node => {
                    conn.close(0u32.into(), b"duplicate");
                    continue;
                }
                Some(p) => {
                    if let Some(old) = p.conn.replace(conn.clone()) {
                        old.close(0u32.into(), b"duplicate");
                    }
                }
                None => return,
            }
        }
        // requests it makes on this connection are answered too
        tokio::spawn(serve(state.clone(), conn.clone()));
        tokio::select! {
            _ = stop.notified() => { conn.close(0u32.into(), b"unpaired"); return; }
            _ = watch(&state, node, &conn) => {}
        }
    }
}

fn allow(state: &Arc<State>, ep: &Endpoint, nodes: &[Value]) {
    let wanted: HashSet<EndpointId> = nodes.iter().filter_map(|v| v.as_str()?.parse().ok()).collect();
    let mut allowed = state.allowed.lock().unwrap();
    for (id, peer) in allowed.iter() {
        if !wanted.contains(id) {
            peer.stop.notify_one();
            if let Some(c) = &peer.conn {
                c.close(0u32.into(), b"unpaired");
            }
        }
    }
    allowed.retain(|id, _| wanted.contains(id));
    for id in wanted {
        if id == ep.id() || allowed.contains_key(&id) {
            continue;
        }
        let peer = Peer::default();
        tokio::spawn(keep_up(state.clone(), ep.clone(), id, peer.wake.clone(), peer.stop.clone()));
        allowed.insert(id, peer);
    }
}

// --- asking ---

async fn call(state: &State, ep: &Endpoint, node: EndpointId, req: Value) -> Result<Value> {
    if !state.is_allowed(&node) {
        bail!("not paired");
    }
    let conn = match state.conn(&node) {
        Some(c) => c,
        None => tokio::time::timeout(DIAL_TIMEOUT, ep.connect(EndpointAddr::from(node), ALPN))
            .await
            .context("unreachable")??,
    };
    let (mut send, mut recv) = conn.open_bi().await?;
    send.write_all(&pack(&req)).await?;
    send.finish()?;
    let data = recv.read_to_end(MAX_ANSWER).await?;
    let answer = unpack(&data, MAX_ANSWER * 4)?;
    if let Some(e) = answer.get("error").and_then(|e| e.as_str()) {
        bail!("{e}");
    }
    Ok(answer.get("res").cloned().unwrap_or(Value::Null))
}

fn stats(state: &State) -> Value {
    let spent = state.spent.lock().unwrap().clone();
    let allowed = state.allowed.lock().unwrap();
    let mut per = serde_json::Map::new();
    let mut ids: HashSet<EndpointId> = spent.keys().copied().collect();
    ids.extend(allowed.keys().copied());
    let (mut tx, mut rx) = (0, 0);
    for id in ids {
        let (mut t, mut r) = spent.get(&id).copied().unwrap_or_default();
        if let Some(c) = allowed.get(&id).and_then(|p| p.conn.as_ref()) {
            let s = c.stats();
            t += s.udp_tx.bytes;
            r += s.udp_rx.bytes;
        }
        tx += t;
        rx += r;
        per.insert(id.to_string(), json!({"tx": t, "rx": r}));
    }
    json!({"op": "stats", "tx": tx, "rx": rx, "per": per})
}

fn load_key(path: &str) -> Result<SecretKey> {
    if let Ok(text) = std::fs::read_to_string(path) {
        let bytes: Vec<u8> = (0..text.trim().len())
            .step_by(2)
            .filter_map(|i| u8::from_str_radix(text.trim().get(i..i + 2)?, 16).ok())
            .collect();
        if let Ok(arr) = <[u8; 32]>::try_from(bytes.as_slice()) {
            return Ok(SecretKey::from_bytes(&arr));
        }
        bail!("{path} isn't a key");
    }
    let key = SecretKey::generate();
    let hex: String = key.to_bytes().iter().map(|b| format!("{b:02x}")).collect();
    let tmp = format!("{path}.tmp");
    std::fs::write(&tmp, hex)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(0o600))?;
    }
    std::fs::rename(&tmp, path)?;
    Ok(key)
}

#[tokio::main]
async fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let key_path = args
        .windows(2)
        .find(|w| w[0] == "--key")
        .map(|w| w[1].clone())
        .context("usage: musicdeck-net --key <file> [--relay-only] [--no-dht]")?;
    let relay_only = args.iter().any(|a| a == "--relay-only");
    let no_dht = args.iter().any(|a| a == "--no-dht");

    let transport = QuicTransportConfig::builder()
        .keep_alive_interval(KEEP_ALIVE)
        .max_idle_timeout(Some(IDLE.try_into()?))
        .build();
    let mut builder = Endpoint::builder(presets::N0)
        .secret_key(load_key(&key_path)?)
        .alpns(vec![ALPN.to_vec()])
        .transport_config(transport)
        .portmapper_config(PortmapperConfig::Disabled);
    if !no_dht {
        builder = builder.address_lookup(DhtAddressLookup::builder());
    }
    if relay_only {
        // for measuring the relays: no direct paths at all
        builder = builder.clear_ip_transports();
    }
    let ep = builder.bind().await?;

    let (out, mut out_rx) = mpsc::unbounded_channel::<Value>();
    tokio::spawn(async move {
        let mut stdout = tokio::io::stdout();
        while let Some(v) = out_rx.recv().await {
            let mut line = serde_json::to_vec(&v).unwrap_or_default();
            line.push(b'\n');
            if stdout.write_all(&line).await.is_err() || stdout.flush().await.is_err() {
                std::process::exit(0);
            }
        }
    });
    let state = Arc::new(State {
        out,
        allowed: Mutex::new(HashMap::new()),
        asked: Mutex::new(HashMap::new()),
        next_rid: AtomicU64::new(1),
        spent: Mutex::new(HashMap::new()),
    });
    state.say(json!({"op": "ready", "node": ep.id().to_string()}));
    tokio::spawn(accepting(state.clone(), ep.clone()));
    {
        let (state, ep) = (state.clone(), ep.clone());
        tokio::spawn(async move {
            if tokio::time::timeout(Duration::from_secs(30), ep.online()).await.is_err() {
                state.log("no relay reachable yet: only devices on this network can be found");
            } else {
                state.log("online");
            }
        });
    }

    let mut lines = BufReader::new(tokio::io::stdin()).lines();
    while let Some(line) = lines.next_line().await? {
        if line.len() > 2 * MAX_ANSWER {
            continue;
        }
        let Ok(msg) = serde_json::from_str::<Value>(&line) else { continue };
        match msg.get("op").and_then(|o| o.as_str()) {
            Some("allow") => {
                let nodes = msg.get("nodes").and_then(|n| n.as_array()).cloned().unwrap_or_default();
                allow(&state, &ep, &nodes);
            }
            Some("call") => {
                let id = msg.get("id").cloned().unwrap_or(Value::Null);
                let node = msg.get("node").and_then(|n| n.as_str()).and_then(|n| n.parse::<EndpointId>().ok());
                let req = msg.get("req").cloned().unwrap_or(Value::Null);
                let (state, ep) = (state.clone(), ep.clone());
                tokio::spawn(async move {
                    let result = match node {
                        Some(node) => call(&state, &ep, node, req).await,
                        None => Err(anyhow::anyhow!("bad device id")),
                    };
                    state.say(match result {
                        Ok(res) => json!({"op": "result", "id": id, "res": res}),
                        Err(e) => json!({"op": "result", "id": id, "error": e.to_string()}),
                    });
                });
            }
            Some("reply") => {
                let rid = msg.get("rid").and_then(|r| r.as_u64()).unwrap_or(0);
                if let Some(tx) = state.asked.lock().unwrap().remove(&rid) {
                    let answer = match msg.get("error") {
                        Some(e) => json!({"error": e}),
                        None => json!({"res": msg.get("res").cloned().unwrap_or(Value::Null)}),
                    };
                    let _ = tx.send(answer);
                }
            }
            Some("stats") => state.say(stats(&state)),
            Some("quit") => break,
            _ => {}
        }
    }
    // stdin closed: Python is gone
    ep.close().await;
    Ok(())
}
