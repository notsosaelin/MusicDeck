# MusicDeck

A [Decky Loader](https://decky.xyz) plugin for SteamOS, Bazzite and other SteamOS-like systems. It turns the
**Soundtracks** tab of the Quick Access Menu into a music player that keeps playing while you're in a game.

## Features

- **Seven sources:** local files, Navidrome/Subsonic, SoundCloud, YouTube Music (plus regular YouTube), Qobuz
  and Spotify. One source is live at a time, chosen in the plugin's Decky page.
- **Native Quick Access tab:** now playing, Library, Search, Lyrics, Queue and Equalizer, built from Steam's
  own components and controller conventions.
- **MusicDeck playlists** for every source, stored on the device, with custom artwork. Save any playlist or
  album from a source as your own.
- **Synced lyrics:** the source's own lyrics, otherwise [LRCLIB](https://lrclib.net).
- **Sound:** 10-band EQ with presets, volume levelling from ReplayGain data, and endless radio when the
  queue runs out.
- **Picks up where you left off:** the queue survives restarts, and with Navidrome it follows you between
  devices.

## Sources

| Source | Account | What you get |
| --- | --- | --- |
| Local files | — | Recursive scan, tags and artwork, `.lrc`/embedded lyrics, ReplayGain, `.m3u` playlists, favourites, radio |
| Navidrome / Subsonic | Your server | Favourites, play reports, lyrics, similar-song radio, queue sync across devices, add to server playlists, ReplayGain |
| SoundCloud | None | Search, curated selections, artists, playlists and albums, related-track radio |
| YouTube Music | None | Home, search across YouTube Music **and** regular YouTube, artists and channels, radio, lyrics |
| Qobuz | Your subscription | Your playlists and favourites (synced), editorial picks, search, MP3 up to 24-bit/192 kHz FLAC, plays reported as from the official player |
| Spotify | Premium | Your playlists, Liked Songs (synced), recently played, top tracks and artists, saved albums, new releases, search, Spotify's synced lyrics, up to 320 kbps, Spotify's own loudness levelling |

**No accounts where a service doesn't need one.** SoundCloud and YouTube are used signed out, as NewPipe
does: nothing ties your listening to an identity, and there's no account to get flagged for using an
unofficial client. Your playlists and favourites for those stay on the Deck.

**Passwords are never stored.** Navidrome passwords become Subsonic tokens on save. Qobuz and Spotify sign in
on their own websites, in Steam's browser, and MusicDeck only receives the resulting tokens.

## Using it

| Where | What |
| --- | --- |
| Decky → MusicDeck | Add and manage sources, pick the live one, playback options, debug log |
| Quick Access → Soundtracks tab | The player. Takes over the tab by default, or gets its own tab |

Controller: **A** plays or opens, **X** adds a track to the queue (removes it, in the Queue), **Y** opens a
track's or playlist's options, **B** goes back. Re-selecting Library or Search returns to its start.

Switching the live source stops playback and clears the queue; nothing from the previous source stays queued.

## Install

Until it's in the Decky store: enable Decky's developer mode, then **Decky → Settings → Developer → Install
plugin from ZIP** with `MusicDeck.zip`.

Logs are in `~/homebrew/logs/MusicDeck/`. Turn on **Debug log** under Troubleshooting in the plugin settings
for a detailed playback timeline in `musicdeck-debug.log`.

## How it works

```
Steam UI (shared JS context, always loaded)          Decky plugin process (Python)
┌──────────────────────────────────────────┐        ┌───────────────────────────────────┐
│ QAM patch → MusicPanel (React)           │ calls  │ main.py  Plugin                   │
│ Player: <audio>/hls.js → ReplayGain →    │───────▶│   services/ local, subsonic,      │
│   preamp → 10× biquad → destination      │        │     soundcloud, youtube, qobuz    │
│                                          │  HTTP  │ server.py  127.0.0.1:<random>     │
│   audio.src = /stream/<svc>/<id>?k=…     │◀──────│   files, proxied streams, HLS     │
└──────────────────────────────────────────┘        └──────────────┬────────────────────┘
                                                                   │ JSON lines
                                                    ┌──────────────▼────────────────────┐
                                                    │ ytdlp_worker.py (system Python)   │
                                                    │   yt-dlp  ── bin/qjs (QuickJS-NG) │
                                                    └───────────────────────────────────┘
```

- **Playback** runs in Steam's own browser context, so it keeps going when the QAM closes or a game runs.
- **All audio goes through the loopback server**, keyed per session. Local files can't be opened from the UI
  context, and remote streams need CORS headers or the Web Audio EQ outputs silence. HLS sources
  (SoundCloud) are served as rewritten playlists so hls.js fetches every segment through it too. Signed URLs
  are cached only until their real expiry; an expired playlist is re-resolved server-side. If playback
  stalls or a stream errors mid-song, the player re-resolves and resumes at the same second once before
  reporting a failure.
- **YouTube** browsing uses [ytmusicapi](https://github.com/sigma67/ytmusicapi); audio comes from
  [yt-dlp](https://github.com/yt-dlp/yt-dlp) in a persistent worker on the system's Python, because Decky's
  frozen interpreter lacks parts of the standard library yt-dlp imports. yt-dlp first tries without a
  JavaScript engine (~1 s) and falls back to the bundled [QuickJS-NG](https://github.com/quickjs-ng/quickjs)
  (~5 s) when YouTube insists on its signature challenge. yt-dlp updates itself from PyPI (checksum-verified,
  weekly check or on demand, rolled back if the new version won't start).
- **Qobuz** follows [Qobine](https://github.com/sofusa/qobine) for sign-in (Qobuz's OAuth page, caught by a
  one-shot loopback listener) and [streamrip](https://github.com/nathom/streamrip) for the web player's app
  credentials and signed file URLs.
- **Spotify** follows [Spotifast](https://github.com/crmne/spotifast): two browser sign-ins (playback with
  Spotify's desktop identity, library access with the public Web API app or your own), and
  [librespot](https://github.com/librespot-org/librespot) for audio. Playlists, your playlist library,
  albums, artists and lyrics are read over the librespot session, the way Spotify's own clients read them,
  so they don't touch the Web API's rate limit. The shared Web API app is rate-limited across everyone using
  it; for the rest (search, Liked Songs, recently played, top tracks), add your own app's client ID to the
  source (developer.spotify.com, redirect URI `http://127.0.0.1:8989/login`). `bin/spotify-helper` (Rust,
  `backend/spotify-helper/`) keeps the librespot session and serves each track's *decrypted original file*
  (Ogg Vorbis) over loopback HTTP with byte ranges, so Steam's browser decodes it like any other source. It's
  a fully static binary (musl, rustls with bundled roots), so it runs on any distro.
- **QAM takeover** (`src/qam.tsx`) wraps Decky's own tab hook and swaps the Soundtracks tab's panel and title,
  or adds a separate tab. Reloads hand over cleanly between plugin instances. If Decky changes that internal,
  the patch disables itself and the plugin stays usable from the Decky menu.
- **Services** implement `services/base.py:MusicService`, advertise optional features (favourites, play
  reports, lyrics, radio, queue sync, server playlists) and return one shared data model (`models.py`), so
  the UI never needs to know which source it's showing.

## Developing

Requirements: Node 18+, pnpm 9, Python 3.

```sh
pnpm install
pnpm build                    # dist/index.js
python scripts/vendor.py      # pure-Python deps into py_modules/, QuickJS-NG into bin/ (checksum pinned)
bash scripts/build_spotify_helper.sh   # bin/spotify-helper (needs rustup, cargo-zigbuild, zig; Linux or WSL)
python scripts/package.py     # out/MusicDeck.zip
```

Backend tests need no Deck: they stub `decky`, build a small tagged library, and drive the real plugin and
stream server over HTTP.

```sh
pip install aiohttp certifi
python tests/test_backend.py
```

## Roadmap

Planned, not started:

- **Offline playlists.** "Available offline" on a MusicDeck playlist downloads each track once (SoundCloud
  HLS segments joined into a file, YouTube via yt-dlp); playback prefers the file. Files are tracked per
  track, so playlists sharing a song share the download.

Not possible:

- **Apple Music.** Apple only serves its catalogue as Widevine-protected streams, and Steam's browser engine has
  no Widevine (or any other DRM system), which a plugin can't add. Desktop clients such as
  [Cider](https://github.com/ciderapp/cider) and [Sidra](https://github.com/wimpysworld/sidra) are separate
  apps built on CastLabs Electron, a Chromium that ships Widevine; nothing comparable exists inside Steam.
  Library browsing alone wouldn't make a useful source, and MusicDeck doesn't decrypt streams.

## License

GPL-3.0-or-later. Bundled: mutagen (GPL-2.0+), yt-dlp and yt-dlp-ejs (Unlicense), ytmusicapi (MIT),
requests (Apache-2.0), QuickJS-NG (MIT), hls.js (Apache-2.0), librespot (MIT, in spotify-helper).
