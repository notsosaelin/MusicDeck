# Device presence and handoff over the internet

Built (farlink.py, backend/musicdeck-net): the research it came from, what was measured, and what's left.
Where it lives in the code: ARCHITECTURE.md (farlink.py, Security).

## What's wanted

- Pair MusicDeck installs once; each has a persistent cryptographic identity.
- On start, find which paired installs are reachable now (LAN or internet), with no Tailscale, port forwarding,
  UPnP/NAT-PMP/PCP or VPN app; across changing IPs, routers, CGNAT and different Wi-Fi.
- A controller-friendly device list: **Online, something to resume** / **Online, nothing to resume** / **Offline**.
  An unreachable device disappears promptly.
- Each device owns its session (source, track id, position, playing or not). Nothing global, nothing merged, never
  overwrite another device's playback because it came online. The receiver fetches fresh session data from the
  chosen peer before offering **Jump Back In**, and only when it has that source; Local files and discs never
  transfer; playback never starts by itself.
- Security: secure pairing, authenticated connections, a trusted-peer allowlist, revocation, strict validation of
  every incoming message. Never expose Decky RPC or anything else of the backend.
- **No configuration** for the user, and **no cost to the user or to the developer**: no account, no server to rent.
  The existing LAN/Tailscale path keeps working.
- Play-history and playlist sync: keep, if it rides on the same channel without major reworking (decided
  2026-10-30). History sync isn't a guarantee or a goal in itself.

## Where it starts from (peers.py, 0.41.x)

LAN multicast announce (239.255.77.82:41377), X25519 pairing with six-digit numeric comparison, HMAC-signed HTTP on
port 41378, home-network addresses only, no encryption of the traffic (Decky's Python has no fast cipher). Carries
sessions (Continue listening), casting to another device, play history (plays.jsonl pages), playlists (shared()/take()
in playlists.py), and Local files streamed between devices.

## Options looked at

| Option | NAT traversal / relay | Needs | Verdict |
|---|---|---|---|
| **iroh** (Rust; MIT/Apache; n0) | QUIC with hole punching; encrypted relay fallback; node id = ed25519 public key; discovery by DNS/pkarr | relay + discovery servers: n0 runs free public ones (or self-host) | **Chosen direction** |
| rust-libp2p | DCUtR hole punching, circuit relay v2 | relays you run; more moving parts | workable, heavier |
| Tailscale tsnet / Headscale | WireGuard | account or coordination server | excluded (no Tailscale/accounts) |
| Hyperswarm (HyperDHT) | DHT hole punching; weak relay story | a Node.js runtime bundled | poor fit |
| WebRTC | STUN/TURN | signalling server; compiled libs Decky's Python can't load | poor fit |
| Nebula / plain WireGuard | needs a lighthouse/endpoint with a public IP | a server | excluded (cost) |

## Planned shape

- A small Rust sidecar (`bin/<platform>/musicdeck-net`), built like spotify-helper (static musl x86_64/arm64, Windows
  gnu; `scripts/build_*.sh` in WSL). ~8-12 MB per platform. Python talks to it over JSON lines on stdin/stdout.
- It holds the install's iroh secret key (in the settings folder), and the allowlist of paired node ids.
- One ALPN, e.g. `musicdeck/1`, with a handful of message types (hello/presence, get-session, then later plays and
  playlists pages), each length-limited and schema-checked before Python sees it. Nothing else is served.
- Pairing: reuse today's numeric comparison when both are on the LAN, and add a short pairing code/ticket for
  pairing across the internet; pairing exchanges and pins the iroh node ids. Unpair = drop from the allowlist + tell
  the other side.
- Presence: dial every paired node at start and keep one connection each with a short idle timeout (QUIC keepalive),
  so a device that goes away shows Offline within seconds; redial with backoff.
- Handoff: on the receiving side, ask the chosen peer for its session right before showing Jump Back In; offer it
  only if this device has the same source (catalogue types; never local/disc).
- No settings to change. n0's public relays and discovery are used as they come.

## What n0's free relays are (checked 2026-10-30)

- n0's docs: the public relays are "free to use", "shared across all iroh developers worldwide", rate-limited
  "to prevent abuse", with "no uptime or performance guarantees", and "suitable for development and testing";
  for production they recommend their paid Shared Relays (Pro, $19/month) or dedicated ones, or self-hosting.
  (https://docs.iroh.computer/concepts/relays)
- Their blog (2026-09-08): the free relays are rate-limited "based on the total current unauthenticated traffic"
  (no numbers published), enforced "more strictly" as iroh got popular; iroh 1.1+ tells an endpoint when it's
  being rate-limited. Not said whether that throttles or blocks. (https://www.iroh.computer/blog/shared-relays)
- Relays do two jobs: help two devices find each other and time the hole punch, and carry traffic when no direct
  path can be made (roughly 1 network in 10). Without any relay, devices behind NAT mostly can't reach each other.
- Discovery: n0's DNS/pkarr service (also n0-run), or the Mainline DHT (BitTorrent's; no operator, free; optional
  in iroh), or local-network lookup.
- Self-hosting a relay: one open-source binary on a server with a public IP and a DNS name; stateless; TLS built
  in; can be limited to known endpoint ids. Costs a server (some clouds have always-free tiers, with an account).

So "no cost to anyone" and "no configuration" points at n0's free relays, which n0 labels for development and
testing, with unpublished limits that have been tightening. MusicDeck's traffic is tiny (see the estimate below),
so throttling by volume should rarely matter, but nothing guarantees it stays available.

## Decision (2026-10-30)

n0's free relays and discovery, the Mainline DHT published to as well (so finding a device doesn't rest on n0
alone), LAN as today when neither works. A hidden setting may point at a self-hosted relay later. No setup.

## Risks and weak points

What holds whatever the route: QUIC encrypted end to end and authenticated by each device's key, so no relay or
DHT node can read, change or impersonate MusicDeck's traffic; only paired endpoint ids are answered, about the few
things allowed (presence, session, playlists, plays), every message size- and schema-checked; nothing of Decky's
RPC or the rest of the backend is reachable.

- **n0**: availability (free, no SLA, rate limits shared by all unauthenticated users, unpublished and
  tightening; could require accounts or end: then internet presence stops and LAN carries on); metadata (a relay
  sees both ends' IPs, timing and volume; the DNS/pkarr records publish a device's current address and relay under
  its id, so anyone who learns the id can look up its IP); a relay can drop or delay traffic (denial of service),
  not read or forge it; reliance on one company and its jurisdiction.
- **Mainline DHT**: public (records readable by anyone with the id; DHT nodes, which anyone runs and crawlers log,
  see the publishing and querying IPs); unreliable (records expire and need republishing, lookups take seconds to
  a minute or fail, some networks block or flag DHT traffic); Sybil/eclipse nodes can withhold records (denial of
  service) but can't forge them (signed by the device's key).
- **MusicDeck's own**: each device's secret key in the private settings folder: stolen, it lets someone pose as
  that device to the others (session, playlists, plays; Local files streaming if it were allowed over the
  internet, which it isn't); unpairing revokes it. A UDP port listens, so strangers can attempt connections
  (refused after the handshake) or flood it like any open port. New dependencies (iroh's tree).
- **Mitigations**: device ids only shared between paired devices; no Local files or disc audio over the internet;
  strict message limits; immediate revocation on unpair; DHT as a backup only; no UPnP or port forwarding.

## Measured (2026-10-30, scripts/net_test.py and the PC and the Ally)

| | direct | through n0's relay only |
|---|---|---|
| session fetch (Jump back in) | 638 B, ~20 ms | 562 B, ~100 ms |
| playlist sync, 400 songs (64 KB of JSON, deflated) | 6.4 KB | 6.3 KB |
| idle presence, per paired device that's on | ~175 KB/hour | ~165 KB/hour |
| Offline shown after a device vanishes | ~28 s | ~25 s |
| Continue (pause there, read, play here) | 119-129 ms | 178-301 ms |

The idle cost is iroh's own: its per-path keep-alive is fixed at 5 s or less (QuicTransportConfig ignores more),
and hole punching retries every 5 s while only the relay works. About 4 MB a day for two devices on all day, none
while one sleeps. A device on a phone hotspot was reached from home (hole punched, then direct). Continue lands on
the same second it paused at; a change of what's playing shows on the others within ~1 s (`/p/session/changed`).

## Still to find out

1. (done: see above and "What n0's free relays are") **n0's public relays and discovery: what's allowed and how limited.** n0 describes them as free for anyone; check
   their current terms/docs for rate limits, bandwidth caps, and whether they're meant for production use, and what
   happens when limits are hit (degrade to direct-only? to LAN-only?). Nothing may cost the user or the developer,
   so if the free service can't be relied on, the fallback is LAN (as today) rather than a paid relay.
2. **How much traffic MusicDeck would cause** (estimate below; measure it): run two sidecars, one behind CGNAT or a
   phone hotspot, force relay (block UDP hole punching) and count bytes for: idle presence per hour, one session
   fetch, one play-history page, one playlist sync, and a pairing.
3. How fast "Offline" shows when a device sleeps, loses Wi-Fi, or Steam quits.
4. Hole-punch success rate on the networks at hand (home router, phone hotspot, a CGNAT ISP).

### Rough traffic estimate (to check with the tests)

- Presence keepalive: a QUIC ping each way every ~10-15 s, ~50-100 B each: ~25-70 KB per peer per hour.
- A session fetch: ~1-2 KB. Pairing: a few KB once.
- Play-history sync: a few hundred bytes per listen (a page of new plays only). Playlist sync: the changed
  playlist's JSON, typically 5-100 KB.
- No audio goes over it: Local files and discs don't hand off, and catalogue songs play from their own service on
  the receiving device. That keeps relayed traffic tiny; streaming Local files across the internet would not be.
