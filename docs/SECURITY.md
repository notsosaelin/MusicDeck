# MusicDeck security

How MusicDeck protects the accounts and data it handles, what an audit of every entry point found
(October 2026), what was fixed, and what risk remains. For how the pieces fit together, see
[ARCHITECTURE.md](ARCHITECTURE.md#security-model).

## What's worth protecting

| Asset | Where it lives |
| --- | --- |
| Sign-ins: access and refresh tokens, Subsonic salted tokens, Spotify's stored playback credentials, the ListenBrainz token, the Last.fm API secret and session key (never sent to the frontend) | `settings.json` (mode 600) and `data/spotify/accounts/<user>/`, in folders made 0700 at start-up |
| The signed-in sessions themselves (a Premium account playing through the Spotify helper) | the helper process |
| Listening history, playlists, favourites, where books and episodes were left | MusicDeck's settings and data folders |
| The Deck itself (its disk, other programs, its network) | — |

## Who could try

- **Another program on the Deck**, running as the same or another user.
- **A web page** open in Steam's browser or any browser on the Deck (it can send requests to
  `127.0.0.1`, but can't read the answers without CORS).
- **Someone on the same network** (café Wi-Fi), watching traffic or connecting to the Deck.
- **A hostile or compromised server or feed**: a podcast feed, a radio station, a shared playlist,
  a source's API answering with nonsense or too much data.
- **A file the user opens**: a backup someone else made.
- **Whoever reads a debug log** the user posted with a bug report.
- **Another Decky plugin** in Steam's shared JavaScript context.

## Entry points and how each is guarded

| Entry point | Guard |
| --- | --- |
| Stream server (`/stream`, `/art`, `/fetch`) | Bound to 127.0.0.1 on a random port; every request needs the per-session key (constant-time compare). Credentials for upstream requests are added as headers here, never put in URLs the UI sees. |
| Casting site (while casting only) | Bound to the address on the home network that reaches the device, on a random port; answers only the device's own address; each track gets a fresh random link (four kept); its HLS relay only fetches hosts that track's own playlists named. Closed when casting stops. The Deck-only routes and key aren't on it. |
| Your devices (`peers.py`, port 41378 TCP, 41377 UDP) | Answers home-network addresses only. Pairing requests only while the user has Your devices open (2 minutes), one prompt per address per 10 s, and only accepted by the person (X25519 and a six-digit code on both screens: a passive listener learns nothing, a device in the middle makes the codes differ). After pairing, every request is HMAC-SHA256 signed with the pair's key over method, path, time, nonce and body, refused outside 5 minutes or when seen before; song and picture links are signed per item and expire in 6 hours, and a device never relays another device's songs. Handed-over queues are reshaped like saved sessions. On Windows the firewall rule MusicDeck asks for allows its two ports from the local subnet only. |
| Cast and UPnP devices | Devices are found on the local network and are untrusted: replies are size-limited, XML with entity declarations is refused, UPnP descriptions must live on the device that answered, Cast messages over 1 MB end the connection. Spotify is never cast (it would mean handing decrypted audio to another device). |
| Spotify helper's audio server | Bound to 127.0.0.1; every request needs a per-start key, sent as the `X-MusicDeck-Key` header (fixed in this audit; before, any local program could play through the account). |
| Sign-in listeners (Qobuz, Spotify) | Bound to 127.0.0.1, only while a sign-in is open, closed after it. Spotify checks a random `state` and uses PKCE. Pages they answer with echo nothing from the request. |
| Backend endpoints (`callable`) | Every argument is treated as untrusted: ids are checked against each source's id format before reaching a URL path or a tool (`[\w-]` for Audiobookshelf, digits for Plex rating keys and Steam app ids, YouTube's 11-character video ids and `UC…` channel ids; for saving to a library, letters and digits for Spotify and Qobuz (no comma adds another item), `[A-Za-z0-9-]` for TIDAL, hex for Jellyfin and Emby), playlist tracks keep only well-formed fields, settings only take known fields of the right kind. The test suite's "abuse" section calls every endpoint with garbage, stale ids and double taps. |
| Sources' answers (APIs, feeds, pages) | Parsed defensively; failures become messages, not crashes. Downloads stop past 4 GB per file and when free space falls under 512 MB; HLS that's encrypted or isn't audio isn't saved. |
| Backup files | Refused whole unless they're a MusicDeck backup; each part checked (known source types, 12-hex ids, image types and sizes, only MusicDeck's own data-file names, no secrets kept even if a file carries some). Never overwrites what's there. |
| Files written | Settings, playlists and indexes are written atomically through `mkstemp` (mode 600). Downloaded files are named by hash, inside the data folder. A backup is written mode 600. |
| Logs | `debuglog.redact` takes the query out of every web address and masks credential-looking values in the debug log, the main log (tracebacks included) and every error message the UI shows. |
| The UI | Text from sources is rendered as text (no HTML injection points). Images load only through the stream server. A page's button only runs `steam://install/<number>` or opens an `https://` page. |
| Helper processes (yt-dlp worker, Spotify helper) | Started with argument lists (no shell); the yt-dlp worker only answers a fixed set of operations and YouTube Music methods. |
| Third-party code | Python packages pinned by version and file hash (`pip --require-hashes`); QuickJS pinned by checksum; the Spotify helper built from source with a locked `Cargo.lock`. yt-dlp's self-update checks wheel hashes from PyPI and rolls back if the new copy doesn't start. |
| Network | Every HTTPS connection verifies certificates (certifi's bundle; nothing disables verification). The add-source dialog warns when a plain `http://` address outside the home network would send a password unencrypted. |

## Findings of the October 2026 audit

| # | Finding | Severity | Status |
| --- | --- | --- | --- |
| 1 | The Spotify helper's loopback audio server answered anyone: another program, or a web page that found the port, could stream through the signed-in Premium account. | High | Fixed: per-start key, checked in constant time; a helper started without one refuses everything. |
| 2 | With the debug log on, failed stream requests logged full URLs; for Navidrome those carry the salted token (enough to sign in as the user). The frontend's player events logged stream URLs with the stream server's key. Error messages built from network errors could carry URLs too. | High (logs get shared in bug reports) | Fixed: redaction everywhere a message is written or shown; tested. |
| 3 | On distributions whose home folders other users can read, MusicDeck's data and log folders were readable (Spotify's stored credentials live there). SteamOS and Bazzite home folders are private, so the Deck wasn't exposed. | Medium | Fixed: the three folders are made 0700 at start-up. |
| 4 | A source sending endless data could fill the disk during a download (free space was only checked between songs). | Medium | Fixed: checked while writing; 4 GB per-file cap. |
| 5 | YouTube ids from the UI went into yt-dlp's URL unchecked; Steam app ids into `steam://install/` likewise; the UI would run any `steam://` command a page named. | Low (yt-dlp only reads youtube.com; app ids come from Steam) | Fixed: ids checked in the backend and the worker; the UI allows only install links and https pages. |
| 6 | Vendored packages were pinned by version only. | Low | Fixed: pinned by hash. |
| 7 | A plain `http://` server address on the internet sends the password unencrypted, without a word. | Low (the user's choice) | Fixed: the dialog warns. |
| 8 | Restored playlists and backup data could carry fields of the wrong type that broke a page. | Low | Fixed: tracks and settings are reshaped to their fields and kinds. |

## Remaining risk, by design or by platform

- **Tokens are stored unencrypted** (mode 600, private folders). SteamOS's game mode has no system
  keyring, and a key kept beside them would protect nothing. Anything running as the user can
  read them, as with every app's saved sign-ins.
- **Other Decky plugins** share Steam's JavaScript context and can call any plugin's backend
  methods; Decky has no isolation between plugins. None of MusicDeck's methods hand out secrets,
  but another plugin could, say, change settings or start playback.
- **Qobuz's sign-in has no `state` parameter**, so while the sign-in page is open, a local program
  that found the listener's port could complete it with its own code (signing MusicDeck into the
  wrong account). The window is a minute or two on a random port.
- **Feeds and pages name their own images and audio** (podcast art, radio streams), which the
  backend fetches like a browser would: a hostile feed could make the Deck send a GET request to
  a device on its network, without credentials and without seeing the answer in a useful way.
- **Casting is as private as the home network.** Cast devices present self-signed certificates, so
  the Cast connection is encrypted but not authenticated (as for every Cast sender); UPnP control and
  the casting site are plain HTTP, as the protocols are. Someone on the same network could see what's
  playing and, from the device's address, fetch the current track while casting lasts.
- **Your devices talk in plain HTTP** on the home network (signed, not encrypted: Decky's Python has no fast
  cipher). Someone on the same network could see which songs pass between them, what's in a listening
  history being synced and the playlists kept the same on each, but not change anything or ask for anything.
  A paired device can change this one's playlists (that's the point of keeping them the same); what it sends is
  checked as a backup's playlists are, and its songs only ever stream from the device they're on.
- **Plex servers' plain-HTTP local addresses** are tried with the server token, as Plex's own apps
  do on a home network.

## Reporting a problem

Open an issue without details, or contact the maintainer privately, and include what an attacker
needs (a program on the Deck, a web page, a network position, a file the user opens). Please don't
post logs that predate the redaction above without checking them for tokens first.
