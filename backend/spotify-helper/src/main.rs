//! MusicDeck's Spotify helper.
//!
//! Holds a librespot session (the engine Spotifast and other open-source clients use) and serves
//! each track as its *decrypted original file* (Ogg Vorbis, or MP3) over loopback HTTP, with byte
//! ranges. Decoding stays in Steam's browser engine, so MusicDeck's player, EQ, levelling and
//! seeking work exactly as for every other source.
//!
//! Control: one JSON object per line on stdin, one reply per line on stdout, matched by "id".
//!   {"id", "op": "login_token", "token"}  sign in with a Spotify playback access token; librespot
//!                                          turns it into reusable credentials kept in the cache
//!   {"id", "op": "login_cached"}          sign in with those stored credentials
//!   {"id", "op": "status"}                {"signed_in", "username"}
//!   {"id", "op": "logout"}                forget the stored credentials
//!   {"id", "op": "playlist"|"album"|"artist"|"lyrics", "target"}, {"id", "op": "rootlist"},
//!   {"id", "op": "profile"}, {"id", "op": "tracks", "ids"}          catalogue reads over the session (see reads.rs)
//! The first line printed is {"ready": true, "port": N}; audio is served at
//!   GET http://127.0.0.1:N/track/<base62 id>?bitrate=96|160|320
//!   GET http://127.0.0.1:N/info/<base62 id>   -> {"format", "length", "gain": {...}}
//! Every request must carry the X-MusicDeck-Key header with the key MusicDeck started the helper
//! with (env MUSICDECK_HELPER_KEY): without it, any program on the device, or a web page guessing
//! the port, could play through the signed-in account.

mod reads;

use std::collections::HashMap;
use std::io::{self, BufRead, Read, Seek, SeekFrom, Write};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::thread;

use librespot_audio::{AudioDecrypt, AudioFile};
use librespot_core::audio_key::AudioKey;
use librespot_core::authentication::Credentials;
use librespot_core::cache::Cache;
use librespot_core::config::SessionConfig;
use librespot_core::session::Session;
use librespot_core::spotify_id::SpotifyId;
use librespot_core::spotify_uri::SpotifyUri;
use librespot_core::FileId;
use librespot_metadata::audio::{AudioFileFormat, AudioFiles, AudioItem};
use serde_json::{json, Value};
use tiny_http::{Header, Method, Response, Server, StatusCode};
use tokio::runtime::Runtime;

/// Spotify prefixes its Ogg files with a custom header (holding loudness data); the real Ogg
/// stream starts here, exactly as librespot's player skips it.
const SPOTIFY_OGG_HEADER_END: u64 = 0xa7;

struct Resolved {
    file_id: FileId,
    key: AudioKey,
    format: AudioFileFormat,
}

struct Helper {
    rt: Runtime,
    cache_dir: PathBuf,
    /// what every audio request must present (see the top of this file)
    key: String,
    session: Mutex<Option<Session>>,
    /// track + bitrate -> file and key, so byte-range requests after the first are cheap
    resolved: Mutex<HashMap<String, Arc<Resolved>>>,
}

type Res<T> = Result<T, String>;

impl Helper {
    fn cache(&self) -> Res<Cache> {
        let creds = self.cache_dir.join("credentials");
        std::fs::create_dir_all(&creds).map_err(|e| e.to_string())?;
        Cache::new(Some(creds), None, None, None).map_err(|e| e.to_string())
    }

    fn connect(&self, credentials: Credentials) -> Res<Session> {
        // librespot spawns its connection tasks on creation, so it must be made inside the runtime
        let _runtime = self.rt.enter();
        let session = Session::new(SessionConfig::default(), Some(self.cache()?));
        self.rt
            .block_on(session.connect(credentials, true))
            .map_err(|e| format!("Spotify sign-in failed: {e}"))?;
        *self.session.lock().unwrap() = Some(session.clone());
        self.resolved.lock().unwrap().clear();
        Ok(session)
    }

    /// The live session; sessions die with their connection, so reconnect from the stored
    /// credentials when needed.
    fn session(&self) -> Res<Session> {
        if let Some(s) = self.session.lock().unwrap().as_ref() {
            if !s.is_invalid() {
                return Ok(s.clone());
            }
        }
        let creds = self.cache()?.credentials().ok_or("Not signed in to Spotify")?;
        self.connect(creds)
    }

    fn read(&self, op: &str, msg: &Value) -> Res<Value> {
        let session = self.session()?;
        let target = msg.get("target").and_then(Value::as_str).unwrap_or_default();
        self.rt.block_on(async {
            match op {
                "rootlist" => reads::rootlist(&session).await,
                "profile" => reads::profile(&session).await,
                "playlist" => reads::playlist(&session, target).await,
                "album" => reads::album(&session, target).await,
                "artist" => reads::artist(&session, target).await,
                "lyrics" => reads::lyrics(&session, target).await,
                _ => {
                    let ids: Vec<String> = msg
                        .get("ids")
                        .and_then(Value::as_array)
                        .map(|a| a.iter().filter_map(|v| v.as_str().map(str::to_string)).collect())
                        .unwrap_or_default();
                    reads::tracks(&session, &ids).await
                }
            }
        })
    }

    fn resolve(&self, id: &str, bitrate: u32) -> Res<Arc<Resolved>> {
        let key = format!("{id}:{bitrate}");
        if let Some(r) = self.resolved.lock().unwrap().get(&key) {
            return Ok(r.clone());
        }
        let session = self.session()?;
        let uri = SpotifyUri::from_uri(&format!("spotify:track:{id}")).map_err(|e| e.to_string())?;
        let track_id: SpotifyId = (&uri).try_into().map_err(|_| "Bad track id".to_string())?;
        let resolved = self.rt.block_on(async {
            let mut item = AudioItem::get_file(&session, uri).await.map_err(|e| e.to_string())?;
            if let Err(e) = &item.availability {
                return Err(format!("Not available: {e}"));
            }
            // region-restricted tracks point at an alternative that is playable here
            if item.files.is_empty() {
                let mut found = None;
                if let Some(alts) = item.alternatives.clone() {
                    for alt in alts.0 {
                        if let Ok(a) = AudioItem::get_file(&session, alt).await {
                            if a.availability.is_ok() && !a.files.is_empty() {
                                found = Some(a);
                                break;
                            }
                        }
                    }
                }
                item = found.ok_or("This track isn't available in your region")?;
            }
            let (format, file_id) = pick_format(&item.files, bitrate).ok_or("No playable format for this track")?;
            let key = session
                .audio_key()
                .request(track_id, file_id)
                .await
                .map_err(|e| format!("Couldn't get the track key: {e}"))?;
            Ok::<_, String>(Resolved { file_id, key, format })
        })?;
        let resolved = Arc::new(resolved);
        self.resolved.lock().unwrap().insert(key, resolved.clone());
        Ok(resolved)
    }

    /// Decrypted file reader positioned at the start of the playable data, plus its length.
    fn open(&self, r: &Resolved) -> Res<(AudioDecrypt<AudioFile>, u64, u64)> {
        let session = self.session()?;
        let file = self
            .rt
            .block_on(AudioFile::open(&session, r.file_id, bytes_per_second(r.format)))
            .map_err(|e| format!("Couldn't open the audio file: {e}"))?;
        let total = file.get_stream_loader_controller().map_err(|e| e.to_string())?.len() as u64;
        let offset = if AudioFiles::is_ogg_vorbis(r.format) { SPOTIFY_OGG_HEADER_END } else { 0 };
        Ok((AudioDecrypt::new(Some(r.key), file), offset, total))
    }
}

fn pick_format(files: &AudioFiles, bitrate: u32) -> Option<(AudioFileFormat, FileId)> {
    use AudioFileFormat::*;
    let order: &[AudioFileFormat] = match bitrate {
        96 => &[OGG_VORBIS_96, MP3_96, OGG_VORBIS_160, MP3_160, MP3_256, OGG_VORBIS_320, MP3_320],
        160 => &[OGG_VORBIS_160, MP3_160, OGG_VORBIS_96, MP3_96, MP3_256, OGG_VORBIS_320, MP3_320],
        _ => &[OGG_VORBIS_320, MP3_320, MP3_256, OGG_VORBIS_160, MP3_160, OGG_VORBIS_96, MP3_96],
    };
    order.iter().find_map(|f| files.0.get(f).map(|id| (*f, *id)))
}

fn bytes_per_second(format: AudioFileFormat) -> usize {
    use AudioFileFormat::*;
    let kbps = match format {
        OGG_VORBIS_96 | MP3_96 => 12.0,
        OGG_VORBIS_160 | MP3_160 => 20.0,
        MP3_256 => 32.0,
        _ => 40.0,
    };
    (kbps * 1024.0) as usize
}

fn mime(format: AudioFileFormat) -> &'static str {
    if AudioFiles::is_ogg_vorbis(format) { "audio/ogg" } else { "audio/mpeg" }
}

/// Spotify's header stores loudness as little-endian f32s right before the Ogg data.
fn read_gain(reader: &mut (impl Read + Seek)) -> Option<Value> {
    let mut buf = [0u8; 16];
    reader.seek(SeekFrom::Start(144)).ok()?;
    reader.read_exact(&mut buf).ok()?;
    let f = |i: usize| f32::from_le_bytes(buf[i..i + 4].try_into().unwrap());
    let (track_gain, track_peak, album_gain, album_peak) = (f(0), f(4), f(8), f(12));
    if !track_gain.is_finite() || track_peak <= 0.0 {
        return None;
    }
    Some(json!({"track": track_gain, "track_peak": track_peak, "album": album_gain, "album_peak": album_peak}))
}

fn parse_range(header: Option<&str>, len: u64) -> Option<(u64, u64)> {
    let spec = header?.strip_prefix("bytes=")?;
    let (a, b) = spec.split_once('-')?;
    let (start, end) = if a.is_empty() {
        let n: u64 = b.parse().ok()?;
        (len.saturating_sub(n), len - 1)
    } else {
        let start: u64 = a.parse().ok()?;
        let end = if b.is_empty() { len - 1 } else { b.parse::<u64>().ok()?.min(len - 1) };
        (start, end)
    };
    (start <= end && start < len).then_some((start, end))
}

fn header(name: &str, value: &str) -> Header {
    Header::from_bytes(name.as_bytes(), value.as_bytes()).unwrap()
}

fn error(status: u16, message: &str) -> Response<io::Cursor<Vec<u8>>> {
    Response::from_string(message).with_status_code(StatusCode(status))
}

enum Reply {
    Json(String),
    Audio { status: u16, mime: &'static str, start: u64, end: u64, len: u64, body: std::io::Take<AudioDecrypt<AudioFile>> },
}

fn serve(helper: &Helper, kind: &str, id: &str, bitrate: u32, range: Option<&str>) -> Res<Reply> {
    let resolved = helper.resolve(id, bitrate)?;
    let (mut reader, offset, total) = helper.open(&resolved)?;
    let len = total.saturating_sub(offset);
    match kind {
        "info" => {
            let gain = if offset > 0 { read_gain(&mut reader) } else { None };
            Ok(Reply::Json(json!({"format": format!("{:?}", resolved.format), "length": len, "gain": gain}).to_string()))
        }
        "track" => {
            if len == 0 {
                return Err("Empty audio file".into());
            }
            let (start, end, status) = match parse_range(range, len) {
                Some((s, e)) => (s, e, 206),
                None => (0, len - 1, 200),
            };
            reader.seek(SeekFrom::Start(offset + start)).map_err(|e| e.to_string())?;
            Ok(Reply::Audio { status, mime: mime(resolved.format), start, end, len, body: reader.take(end - start + 1) })
        }
        _ => Err("404".into()),
    }
}

/// Compares in time independent of where the inputs differ, so the key can't be guessed byte by byte.
fn same(a: &[u8], b: &[u8]) -> bool {
    a.len() == b.len() && a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

fn handle(helper: &Helper, request: tiny_http::Request) {
    let given = request
        .headers()
        .iter()
        .find(|h| h.field.equiv("X-MusicDeck-Key"))
        .map(|h| h.value.as_str().to_string())
        .unwrap_or_default();
    if helper.key.is_empty() || !same(given.as_bytes(), helper.key.as_bytes()) {
        let _ = request.respond(error(403, "Forbidden"));
        return;
    }
    let url = request.url().to_string();
    let (path, query) = url.split_once('?').unwrap_or((&url, ""));
    let bitrate = query
        .split('&')
        .find_map(|kv| kv.strip_prefix("bitrate="))
        .and_then(|v| v.parse().ok())
        .unwrap_or(320);
    let parts: Vec<&str> = path.trim_matches('/').split('/').collect();
    let range = request
        .headers()
        .iter()
        .find(|h| h.field.equiv("Range"))
        .map(|h| h.value.as_str().to_string());

    let result = if request.method() == &Method::Get && parts.len() == 2 {
        serve(helper, parts[0], parts[1], bitrate, range.as_deref())
    } else {
        Err("404".into())
    };
    // the client may close early (seeking, skipping): respond errors are expected and ignored
    let _ = match result {
        Ok(Reply::Json(body)) => {
            request.respond(Response::from_string(body).with_header(header("Content-Type", "application/json")))
        }
        Ok(Reply::Audio { status, mime, start, end, len, body }) => {
            let mut headers = vec![header("Content-Type", mime), header("Accept-Ranges", "bytes")];
            if status == 206 {
                headers.push(header("Content-Range", &format!("bytes {start}-{end}/{len}")));
            }
            let count = (end - start + 1) as usize;
            request.respond(Response::new(StatusCode(status), headers, body, Some(count), None))
        }
        Err(e) => {
            if parts.len() == 2 {
                // a stale key or file reference: drop it so the next attempt resolves afresh
                helper.resolved.lock().unwrap().remove(&format!("{}:{bitrate}", parts[1]));
            }
            let status = if e == "404" { 404 } else if e.contains("Not signed in") { 401 } else { 502 };
            eprintln!("request {path} failed: {e}");
            request.respond(error(status, &e))
        }
    };
}

fn reply(out: &Mutex<io::Stdout>, value: Value) {
    let mut out = out.lock().unwrap();
    let _ = writeln!(out, "{value}");
    let _ = out.flush();
}

fn main() {
    let cache_dir = std::env::args()
        .skip_while(|a| a != "--cache")
        .nth(1)
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    let rt = tokio::runtime::Builder::new_multi_thread().enable_all().build().expect("tokio runtime");
    // no key, no audio: a helper started without one refuses every request
    let key = std::env::var("MUSICDECK_HELPER_KEY").unwrap_or_default();
    let helper = Arc::new(Helper { rt, cache_dir, key, session: Mutex::new(None), resolved: Mutex::new(HashMap::new()) });
    let stdout = Arc::new(Mutex::new(io::stdout()));

    let server = Server::http("127.0.0.1:0").expect("bind loopback");
    let port = server.server_addr().to_ip().map(|a| a.port()).unwrap_or(0);
    {
        let helper = helper.clone();
        thread::spawn(move || {
            for request in server.incoming_requests() {
                let helper = helper.clone();
                thread::spawn(move || handle(&helper, request));
            }
        });
    }
    reply(&stdout, json!({"ready": true, "port": port, "version": env!("CARGO_PKG_VERSION")}));

    for line in io::stdin().lock().lines() {
        let Ok(line) = line else { break };
        let Ok(msg) = serde_json::from_str::<Value>(&line) else { continue };
        let id = msg.get("id").cloned().unwrap_or(Value::Null);
        let helper = helper.clone();
        let stdout = stdout.clone();
        thread::spawn(move || {
            let op = msg.get("op").and_then(Value::as_str).unwrap_or("");
            let result: Res<Value> = match op {
                "login_token" => match msg.get("token").and_then(Value::as_str) {
                    Some(token) => helper
                        .connect(Credentials::with_access_token(token))
                        .map(|s| json!({"username": s.username()})),
                    None => Err("Missing token".into()),
                },
                "login_cached" => helper.session().map(|s| json!({"username": s.username()})),
                "status" => Ok(match helper.session() {
                    Ok(s) => json!({"signed_in": true, "username": s.username()}),
                    Err(_) => json!({"signed_in": false}),
                }),
                "rootlist" | "playlist" | "album" | "artist" | "lyrics" | "tracks" | "profile" => helper.read(op, &msg),
                "logout" => {
                    if let Some(s) = helper.session.lock().unwrap().take() {
                        s.shutdown();
                    }
                    let _ = std::fs::remove_dir_all(helper.cache_dir.join("credentials"));
                    Ok(json!({}))
                }
                _ => Err(format!("Unknown op {op}")),
            };
            match result {
                Ok(value) => reply(&stdout, json!({"id": id, "result": value})),
                Err(e) => reply(&stdout, json!({"id": id, "error": e})),
            }
        });
    }
}
