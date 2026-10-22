# Sources: technical notes

How each source works, what it stores, and where it can break. Each section matches one file in
`py_modules/musicdeck/services/`. Common machinery (stream server, sign-in, data model) is in
[ARCHITECTURE.md](ARCHITECTURE.md).

| Source | Type id | Account | Sign-in | Audio | Features |
| --- | --- | --- | --- | --- | --- |
| Local files | `local` | — | — | Original files | favourites, lyrics, radio |
| Steam soundtracks | `steam` | Steam's own | — | Original files | favourites, lyrics, radio |
| Navidrome / Subsonic | `subsonic` | Server | Password → salted token | Original files (`format=raw`) | favourites, play reports, lyrics, radio, queue sync, playlists |
| Jellyfin | `jellyfin` | Server | Quick Connect or password → access token | Original, server transcodes the rest | favourites, play reports, lyrics, radio, playlists |
| SoundCloud | `soundcloud` | — | — | HLS (AAC/MP3) | radio |
| YouTube Music | `youtube` | — | — | Opus/AAC via yt-dlp | radio, lyrics |
| Bandcamp | `bandcamp` | — | — | MP3 128 kbps | radio, lyrics |
| Internet radio | `radio` | — | — | Station streams (MP3/AAC/Ogg/HLS) | favourite stations |
| Qobuz | `qobuz` | Subscription | OAuth on qobuz.com | FLAC up to 24/192, MP3 | favourites, play reports, playlists |
| TIDAL | `tidal` | Subscription | Device login on link.tidal.com | FLAC 16/44.1, AAC | favourites, radio, lyrics, playlists |
| Spotify | `spotify` | Premium | Two OAuth consents (PKCE) | Ogg Vorbis via librespot | favourites, radio, lyrics, playlists |

"Favourites" means the heart and a favourites list, kept on the service where it has one and on
the Deck otherwise. Every source can also use MusicDeck playlists and ListenBrainz.

**Several accounts.** Every source type can be added more than once. Sources with accounts say who
they are signed in as through `account(config)` (Spotify username, Qobuz/TIDAL user id, server +
user for Navidrome and Jellyfin); saving refuses a second source for an account already added. A
new source with no name gets "Spotify", then "Spotify 2", and so on. Per-account files are kept
apart (Spotify's playback credentials live in `spotify/accounts/<username>/`) and deleted with the
source (`MusicService.remove`).

---

## Local files (`local.py`)

- **Library**: folders chosen in the dialog are scanned recursively for `AUDIO_EXTENSIONS`; tags,
  durations, ReplayGain (Vorbis comments, ID3 `TXXX`, MP4 freeform) and embedded art are read with
  mutagen. The index is cached in `local-<id>.json` and rebuilt when `INDEX_VERSION` changes or the
  user rescans; files whose size and mtime are unchanged are not re-read.
- **Art**: embedded pictures (extracted to `art-cache/`) or `cover.jpg`-style files next to the music.
- **Lyrics**: `.lrc` next to the file, embedded lyric tags, then LRCLIB.
- **Playlists**: `.m3u`/`.m3u8` files in the library (relative paths resolved against the playlist).
- **Favourites**: `local-<id>-favourites.json` on the Deck. **Radio**: same artist and same genre
  first, then the rest of the library, shuffled.
- **Security**: `open("folder", path)` refuses paths outside the configured folders.

## Steam soundtracks (`steam.py`, `src/steam.ts`)

- **What's installed** comes from Steam's own records: `steamapps/libraryfolders.vdf` lists every
  library (internal, SD card, other drives; the native, `~/.steam` and Flatpak Steam folders are all
  looked for), and each library's `appmanifest_<appid>.acf` says what is installed where
  (`parse_vdf` reads Steam's text KeyValues format). Soundtrack apps install to
  `steamapps/music/<installdir>`; `StateFlags` bit 4 means fully installed, so a soundtrack that
  is still downloading shows as "Installing…".
- **Older soundtracks are DLC** of their game: their depot is listed under the game's
  `InstalledDepots` with a `dlcappid`, and the files land inside the game's folder. Only folders
  there named like `Soundtrack`, `OST` or `Music` (three levels deep at most) are scanned, so the
  game's sound effects never show up as music.
- **What's owned** is only known to Steam's UI. `src/steam.ts` reads soundtrack apps (app type
  `0x2000`) from `collectionStore`, their game (`optional_parent_app_id`), Steam's album art
  (`appStore.GetAlbumCoverURLsForApp`) and the running game (`SteamUIStore.MainRunningAppID`), and
  sends them to `Plugin.steam_library` at start-up, when a source is saved, and whenever a game
  starts or stops (`GameSessions.RegisterForAppLifetimeNotifications`). The backend keeps the last
  copy in `steam-<id>-owned.json`. Cover URLs are only fetched from Steam's image hosts.
- **Home**: "From <game>" (the running game's soundtracks), "Recently installed", "Installed",
  Library (favourites, composers, all tracks), then "Not installed".
- **Installing**: a not-installed soundtrack opens as a Page with an `Install` action,
  `steam://install/<appid>`, which the frontend runs with `SteamClient.URL.ExecuteSteamURL`:
  Steam's own install dialog (drive choice, size) appears. Steam changing its library folders'
  modification times triggers a rescan on the next visit (`_check_for_changes`), so the
  soundtrack is playable when the download ends, without a manual rescan.
- **One album per soundtrack**: tracks are grouped by app, not by tags, and named as Steam names
  the soundtrack. Soundtracks often ship the album in several formats (`MP3/`, `FLAC/`, `WAV/`);
  `_pick_formats` keeps FLAC, then M4A, Ogg, MP3, WAV, but only among formats with (nearly) as
  many files as the largest one, so bonus tracks in another format stay. Untagged files take
  their track number from the file name ("01 Intro.wav").
- **Art**: a cover in the soundtrack's folder (usual names first, else the largest image), Steam's
  album art, then a picture embedded in a track. Tags, lyrics and the incremental index reuse
  `local.py`.
- **Stored**: `steam-<id>.json` (index), `steam-<id>-owned.json`, `steam-<id>-favourites.json`.
- Tested on a Bazzite handheld with 51 owned soundtracks: browsing, Steam's art for uninstalled
  ones, installing one through the Install button, playback, and the running-game row.

## Navidrome / Subsonic (`subsonic.py`)

- **Protocol**: the Subsonic REST API 1.16.1 with OpenSubsonic extensions where present.
- **Auth**: the password is turned into `token = md5(password + salt)` in `prepare_config`, per the
  Subsonic spec, and dropped. Requests carry `u`, `t`, `s`.
- **Audio**: `stream?format=raw` (the original file, no transcoding); art via `getCoverArt`.
- **Extras**: `getSimilarSongs2`/`getTopSongs` radio, `getLyricsBySongId` (OpenSubsonic), play
  reports via `scrobble` (which also feeds Last.fm if the server links it), and queue sync with
  `savePlayQueue`/`getPlayQueue` so a queue can be resumed on another device.
- Tested against `demo.navidrome.org`.

## Jellyfin (`jellyfin.py`)

- **Protocol**: Jellyfin's REST API, the one its own apps use. Every request carries the
  `Authorization: MediaBrowser Client=…, DeviceId=…, Token=…` header; MusicDeck appears under the
  server's Devices.
- **Sign-in**: Quick Connect (`/QuickConnect/Initiate` → code shown in the dialog → poll
  `/QuickConnect/Connect` → `/Users/AuthenticateWithQuickConnect`), or `/Users/AuthenticateByName`
  with a password on save. Only the token, user id and the device id the token belongs to are stored.
- **Audio**: `/Audio/{id}/universal` with the containers Chromium decodes, so originals stream
  untouched and anything else (ALAC, WMA) is transcoded to MP3 by the server. Token in a header,
  added by the stream server.
- **Extras**: favourites (`/UserFavoriteItems`, with the pre-10.9 path as fallback), play reports
  (`/Sessions/Playing` then `/UserPlayedItems`), lyrics (`/Audio/{id}/Lyrics`, 10.9+), Instant Mix
  radio, playlist editing, loudness from `NormalizationGain`.
- Tested against `demo.jellyfin.org/stable` (12.2). Emby shares much of the API but is untested.

## SoundCloud (`soundcloud.py`)

- **Protocol**: SoundCloud's v2 web API, as NewPipe uses it. The public web app's `client_id` is
  scraped from soundcloud.com's script bundle (`CLIENT_ID` regex), cached in
  `soundcloud-client.json` for a day, and re-scraped when rejected.
- **Audio**: HLS transcodings. The signed playlist URLs live only minutes; their real expiry is read
  from the CloudFront policy in the URL, and the server re-resolves an expired playlist itself.
- **Unplayable tracks are left out** (`_streams`): Go+ tracks (`policy` SNIP, 30-second snippets)
  and some label releases (`policy` MONETIZE, ad-supported) that SoundCloud only serves as
  `cbc-`/`ctr-encrypted-hls`. Those need Widevine, which Steam's browser engine lacks; a paid
  account doesn't change that.
- **Breaks when**: SoundCloud changes how its bundle embeds the client id (update the regex).

## YouTube Music (`youtube.py`, `ytdlp_worker.py`, `ytdlp_update.py`)

- **Browsing**: ytmusicapi, signed out (YouTube Music's internal API). Search combines songs, music
  videos, albums, artists and community playlists, plus plain YouTube videos via yt-dlp when
  "Include all YouTube videos" is on.
- **Audio**: yt-dlp in a persistent worker on the system Python (Decky's Python lacks modules it
  needs). It first tries without a JavaScript engine, then with the bundled QuickJS-NG (`bin/qjs`,
  checksum-pinned in `scripts/vendor.py`) when YouTube requires its signature challenge.
- **Updates**: YouTube changes often, so yt-dlp updates itself from PyPI (`ytdlp_update.py`): wheel
  checksums verified, the new copy must start, else MusicDeck rolls back.
- **Breaks when**: YouTube changes faster than yt-dlp; the update usually fixes it.

## Bandcamp (`bandcamp.py`)

- **Protocol**: signed out, like NewPipe: the public search API (`autocomplete_elastic`), the
  discover API for genre charts, and the `data-tralbum` JSON Bandcamp embeds in album/track pages.
  Artist discographies are read from the `/music` page.
- **Audio**: the 128 kbps MP3 every visitor can stream; tracks an artist reserves for buyers are
  left out. Links are signed, so pages are cached for 15 minutes only.
- Ids are URLs (album URL, track URL, artist root URL).
- **Breaks when**: Bandcamp changes its page markup (`TRALBUM`, `DISCOGRAPHY_ITEM` regexes).

## Internet radio (`radio.py`)

- **Directory**: Radio Browser (radio-browser.info), a community directory with an open API. A
  mirror is picked at random from `/json/servers`, as the project asks.
- **Home**: popular near you (optional country), trending, most listened, genres (tags).
- **Audio**: the station's own stream; HLS stations go through the HLS rewriter (plain-HTTP segments
  allowed for these). Each play is counted with `/json/url/{uuid}`, per Radio Browser's etiquette.
- **Song on air**: `on_air()` opens the stream with `Icy-MetaData: 1`, reads one metadata block
  (`StreamTitle='…'`), and closes it. The Now Playing view polls every 20 seconds. `parse_on_air()`
  splits "Artist - Title", and also reads the tagged titles of iHeart and other broadcast systems
  (`Artist - text="Song" song_spot="M" amgArtworkURL="…"`, or `title="…",artist="…"`); anything
  not marked as music (`song_spot` other than "M": idents, ads, talk) is skipped. The now-playing
  card then shows song, artist and station next to the song's cover (an image link in `StreamUrl`
  counts too) instead of the station's logo, place and genres.
- **Logos** come from wherever the directory points, and some hosts (DuckDuckGo's icon cache) refuse
  requests without a user agent, so `art()` sends MusicDeck's.
- **Favourites**: stored in this source's config (`favourites`) via `save_config`.

## Qobuz (`qobuz.py`)

- **App credentials**: the web player's app id and signing secrets are read from its bundle
  (`BUNDLE_RE`, `APP_ID_RE`, `SEED_RE`), validated with a signed test request, cached a day in
  `qobuz-app.json`. This is how streamrip and qobuz-dl work.
- **Sign-in**: Qobuz's OAuth page (as Qobine does) with `redirect_url=http://localhost:<port>`; a
  one-shot loopback listener receives the code, exchanged via `oauth/callback` for a user token.
- **Audio**: `track/getFileUrl` (signed with the secret) at the chosen quality returns a direct
  FLAC/MP3 link. Plays are reported with `track/reportStreamingStart`, as the web player does.
- **Breaks when**: Qobuz changes its bundle layout (update the regexes).

## TIDAL (`tidal.py`)

- **Sign-in**: TIDAL's device login, as python-tidal (Mopidy-Tidal) does: `device_authorization`
  gives a link.tidal.com URL the user approves in Steam's browser; the token endpoint is polled.
  Only the refresh token is stored.
- **Audio**: `playbackinfopostpaywall` returns either a "BTS" manifest (one direct FLAC/AAC URL) or
  DASH, which `dash_to_hls()` turns into an HLS playlist of the same fMP4 segments for hls.js.
  Anything marked encrypted is refused rather than decrypted.
- **Quality**: up to FLAC 16-bit/44.1 kHz. TIDAL only grants 24-bit "Max" to its browser-redirect
  sign-in, which Steam's browser can't hand back to a plugin.
- **Extras**: favourites (one `favorites/ids` request marks hearts everywhere), track radio, lyrics
  (synced LRC when available), home page modules (playlists, albums, mixes, tracks), playlist
  editing (with TIDAL's required `If-None-Match` ETag).
- Catalogue calls were tested with an app-only token; sign-in, playback, favourites and lyrics need
  a TIDAL subscription and were not yet tested end to end.

## Spotify (`spotify.py`, `backend/spotify-helper/`)

Modelled on [Spotifast](https://github.com/crmne/spotifast).

- **Two sign-ins in one flow** (OAuth with PKCE, redirect to `http://127.0.0.1:<port>/login`):
  1. *Playback*: Spotify's desktop client identity with the `streaming` scope. The access token is
     handed to the helper once; librespot turns it into reusable credentials, kept per account
     in `spotify/accounts/<username>/` (the sign-in runs in a scratch folder that becomes it).
  2. *Web API*: the public app shared by spotify-player and ncspot, or the user's own developer app
     (Client ID field), for library, search and changes. Only its refresh token is stored.
- **Helper** (`bin/spotify-helper`, Rust, static musl build): JSON-lines control on stdin/stdout;
  `GET /track/<id>` serves the decrypted original Ogg Vorbis file with byte ranges, `/info/<id>`
  returns Spotify's loudness values from the file header.
- **Session reads** (`reads.rs`): playlists, the playlist library (rootlist), albums, artists and
  lyrics are read over the librespot session (Spotify's own client endpoints), not the Web API, so
  they don't count against the shared app's rate limit.
- **Rate limiting** (`_Gate`): two requests in flight per app, a shared `Retry-After` cooldown, and
  fail-fast while it lasts; GETs cached for 3 minutes.
- **Requirements**: Spotify Premium. Some newer accounts can't get audio keys through librespot
  ("audio key error"); that is a librespot/Spotify limitation.
- Build: `scripts/build_spotify_helper.sh` (cargo-zigbuild, static x86_64 musl).

---

## Not possible

These were investigated and can't be supported without DRM circumvention:

- **Apple Music**: Widevine-only streams; Steam's browser engine has no CDM. Cider and Sidra are
  separate CastLabs Electron apps for this reason.
- **Deezer, Amazon Music**: same situation.

## Candidates not implemented yet

- **Plex**: feasible (PIN sign-in at plex.tv/link, `/library/sections`, direct play of parts,
  `/:/scrobble`, sonic "nearest" for radio), but there is no public server to test against.
- **Emby**: Jellyfin's ancestor; `jellyfin.py` could likely serve it with header changes.
- **Podcasts**: possible (iTunes search API + RSS), but RSS would need a regex parser since Decky's
  Python has no `xml` module.
