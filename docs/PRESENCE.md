# Device presence and handoff over the internet (research, not built yet)

Notes for picking this work back up. Nothing here is implemented; today's sync is LAN-only (`peers.py`).

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

## Still to find out (run tests before building)

1. **n0's public relays and discovery: what's allowed and how limited.** n0 describes them as free for anyone; check
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
