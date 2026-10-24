# Sources: technical notes

How each source works, what it stores, and where it can break. Each section matches one file in
`py_modules/musicdeck/services/`. Common machinery (stream server, sign-in, data model) is in
[ARCHITECTURE.md](ARCHITECTURE.md).

| Source | Type id | Account | Sign-in | Audio | Features |
| --- | --- | --- | --- | --- | --- |
| Local files | `local` | — | — | Original files | favourites, lyrics, radio, play counts (on the Deck) |
| Steam soundtracks | `steam` | Steam's own | — | Original files | favourites, lyrics, radio |
| Navidrome / Subsonic | `subsonic` | Server | Password → salted token | Original files (`format=raw`) | favourites, play reports, lyrics, radio, queue sync, playlists |
| Jellyfin | `jellyfin` | Server | Quick Connect or password → access token | Original, server transcodes the rest | favourites, play reports, lyrics, radio, playlists |
| Emby | `emby` | Server | Password → access token | Original, server transcodes the rest | favourites, play reports, radio, playlists |
| Plex | `plex` | plex.tv (optional) | PIN: plex.tv/link code, or Plex's page | Original, server transcodes the rest | favourites, play reports, lyrics, radio, playlists |
| Audiobookshelf | `audiobookshelf` | Server | Password → access + refresh token, or an API key | Original files | resume (`progress`), chapters (as `lyrics`) |
| SoulSync | `soulsync` | Server | An admin API key made in SoulSync | Its library files | — |
| SoundCloud | `soundcloud` | — | — | HLS (AAC/MP3) | likes (on the Deck), radio |
| YouTube Music | `youtube` | — | — | Opus/AAC via yt-dlp | likes (on the Deck), radio, lyrics |
| Bandcamp | `bandcamp` | — | — | MP3 128 kbps | likes (on the Deck), radio, lyrics |
| Internet radio | `radio` | — | — | Station streams (MP3/AAC/Ogg/HLS) | favourite stations |
| Podcasts | `podcasts` | — | — | The episode's own file (usually MP3) | subscriptions, resume (`progress`) |
| Live Music Archive | `livemusic` | — | — | archive.org's MP3s of each recording | favourites (on the Deck), radio |
| Qobuz | `qobuz` | Subscription | OAuth on qobuz.com | FLAC up to 24/192, MP3 | favourites, play reports, playlists |
| TIDAL | `tidal` | Subscription | Device login on link.tidal.com | FLAC 16/44.1, AAC | favourites, radio, lyrics, playlists |
| Spotify | `spotify` | Premium | Two OAuth consents (PKCE) | Ogg Vorbis via librespot | favourites, radio, lyrics, playlists |

"Favourites" means the heart and a favourites list, kept on the service where it has one and on
the Deck otherwise. The track menu names them in each source's own words (`LIKE_WORDS`): Save to Liked
Songs (Spotify), Add to My Collection (TIDAL), Add to your stations (radio), Like (SoundCloud, YouTube
Music, Bandcamp), Add to favourites (the rest). Sources used signed out keep likes on the Deck
(`LocalLikes` in base.py: the tracks themselves, in the source's config, up to 5,000) and list them under
Liked songs; nothing goes to the service. Every source can also use MusicDeck playlists and ListenBrainz.

**Several accounts.** Every source type can be added more than once. Sources with accounts say who
they are signed in as through `account(config)` (Spotify username, Qobuz/TIDAL user id, server +
user for Navidrome, Jellyfin, Emby and Audiobookshelf, account + server for Plex); saving refuses a second source for an account already added. A
new source with no name gets "Spotify", then "Spotify 2", and so on. Per-account files are kept
apart (Spotify's playback credentials live in `spotify/accounts/<username>/`) and deleted with the
source (`MusicService.remove`).

---

## Local files (`local.py`)

- **Library**: folders chosen in the dialog are scanned recursively for `AUDIO_EXTENSIONS`; tags,
  durations, ReplayGain (Vorbis comments, ID3 `TXXX`, MP4 freeform) and embedded art are read with
  mutagen. The index is cached in `local-<id>.json` and rebuilt when `INDEX_VERSION` changes or the
  user rescans; files whose size and mtime are unchanged are not re-read. Each file's `added` time
  (when it entered the library) survives edits to its tags, for Recently added.
- **Tags**: genres (several per file: separate values or "Rock; Pop", "Rock/Pop"; one genre whatever its
  case), the original year over a reissue's (`originaldate`), composer, label (`organization`), the
  compilation flag, and a 0-5 star rating (ID3 `POPM` 0-255 as players write it, through an `EasyID3`
  key registered as "rating"; Vorbis `RATING` 0-100 or 1-5; `FMPS_RATING` 0-1). Quality from the stream
  info: format (an `.m4a` that says ALAC is ALAC), bit depth, sample rate, bitrate, channels.
- **Albums**: by album artist and album; without an album artist (compilations, often), by album and
  folder, so a compilation stays one album; `CD1`/`Disc 2` folders count as their parent. An album's
  artist is its album artist, its one artist, or "Various artists". An artist's page adds the songs
  they have on other albums ("Appears on").
- **Tabs**: Home (Recently added, Recently played, Most played (twice or more), Top rated (four stars
  and up), Not played yet, favourites), Albums, Artists, Genres (tiles), Years (decades), Composers
  (only when a file names one), Folders. `NOW_PLAYING = "library"`: `details()` gives the file's facts
  for the Info panel.
- **Plays**: with "Report plays" on, each listen (`scrobble` with `submission`) counts in
  `local-<id>-plays.json` ({track id: {count, last}}), on the Deck only.
- **Tag editing** (feature `tags`, `tagging.py`): a track's fields, or those an album's tracks share
  (one they differ on shows as mixed and stays per file unless changed). Written to a dot-file copy
  beside the file that then replaces it (the scan skips dot-files); "year" also updates an
  `originaldate` the file has. What each key held before is kept per file in
  `local-<id>-tag-history.json` for one undo. No edits while a scan runs; the index is re-read for the
  files edited, and an album renamed as a whole keeps its own picture.
- **Catalogues** (`catalogues.py`): Apple's (iTunes Search API) and Deezer's public APIs, no account or key,
  searched beside MusicBrainz in Look up (results say their source; ids `itunes:<id>`, `deezer:<id>`)
  with the same release shape, and asked for covers after the Cover Art Archive (Apple's 1200 px first;
  only an album of exactly that title by that artist). Only when sure uses MusicBrainz alone: it tells
  one release from another.
- **MusicBrainz** (`musicbrainz.py`, only when asked): releases searched by album and artist ("Various
  artists" left out), those with the album's track count first; a release's title, album artist, first
  year, top genres, label, and track titles, artists and numbers (per disc). Track by track only when
  the counts match. Applying finds the release's cover when the album has none. One request a second.
- **Artwork** (feature `artwork`): albums' own pictures in `local-<id>-art/<album key>.<ext>`, shown before
  folder covers and embedded art; art ids carry the picture's time (`t:<track>.<mtime>`) so a new one
  isn't taken from the browser's cache. Found covers come from the Cover Art Archive (500 px), only for
  a match whose title is the album's. "Find missing artwork" goes through every named album without a
  picture in the background (`status.art`), and every named artist without one.
- **Artists' pictures** (`pictures.py`): Deezer's public catalogue API, no account or key (metadata only),
  for the artist of exactly that name (case and punctuation aside), the best known of several; Deezer's
  blank picture counts as none. Kept as `local-<id>-art/artist-<artist id>.<ext>`; art id `a:<id>.<mtime>`.
- **Saving lyrics** (feature `tags`): what the lyrics sites have, as an `.lrc` beside the track (`[ar:]`,
  `[ti:]`, `[al:]`, then timed lines when synced), written through a hidden copy; tracks with lyrics already
  (a file, or in their tags) are skipped. For an album at once, or every track in the background
  (`status.lyrics`), half a second apart.
- **New music** (`watch.py`, `_queue_new_albums`, `_import`): inotify watches every folder under the
  roots (through ctypes; new folders watched as they appear), and a change rescans once the folders have
  been quiet for 30 s; a scan also runs hourly and at start-up for what wasn't seen (an SD card filled
  elsewhere, the watch limit reached). Tracks whose paths weren't in the index before are new (none on a
  library's first scan); their albums are queued. Per album, as the source is set (`DEFAULTS`, in the
  source's dialog): covers and the album artist's picture (`auto_art`); MusicBrainz (`auto_tags`):
  "ask" puts an album MusicBrainz has by that title on the review list (`local-<id>-review.json`, Browse's
  To review; looking it up and applying, editing its tags or dismissing it takes it off), "sure" applies
  only when every likely release (same title, artist and track count, score 95+) is one release group,
  and only to empty fields; lyrics for its new tracks (`auto_lyrics`). The source then announces a
  summary (`announce` → the `source_note` event → a toast).
- **Where lyrics are kept** (`lyrics_place`): in `local-<id>-lyrics/<track id>.lrc` (default), or beside
  the song as the same-named `.lrc`, the convention Navidrome (`LyricsPriority`), Jellyfin, Plex,
  foobar2000 and Poweramp read; `[ar:] [ti:] [al:] [length:]` head the file. Reading order: an `.lrc`
  beside the song, its tags, MusicDeck's own, then the lyrics sites.
- **Browse tiles**: `center_tabs` adds what's in each section (counts) and a few covers or artists'
  pictures for its tile, cached until the library or its pictures change.
- **Art**: embedded pictures (extracted to `art-cache/`) or `cover.jpg`-style files next to the music.
- **Lyrics**: `.lrc` next to the file, embedded lyric tags, then the lyrics sites (see below).
- **Playlists**: `.m3u`/`.m3u8` files in the library (relative paths resolved against the playlist).
- **Favourites**: `local-<id>-favourites.json` on the Deck. **Radio**: same artist and same genre
  first, then the rest of the library, shuffled.
- **Security**: `open("folder", path)` refuses paths outside the configured folders.

## Plugged-in devices (`device.py`, `devices.py`)

- **What**: an MP3 player, iPod (disk mode), USB stick or phone, played as a source while it's plugged in
  (not saved; `Plugin.devices`). Read only: Local files' scanner on its folders without tag editing,
  artwork or look-ups (`DeviceService`). Its id comes from the file system's UUID or the phone's MTP name,
  so its index, favourites and playlists are there again next time.
- **USB storage**: SteamOS and Bazzite mount it under `/run/media/<user>/` themselves; `/proc/self/mounts`
  says what's mounted there from a `/dev/sd*` device (the SD card and internal drives aren't "plugged in").
- **Phones (MTP)**: GVfs lists them (`gio mount -li`, with the session bus given explicitly: Decky's
  process doesn't have it); MusicDeck mounts those not yet mounted and plays each storage's Music,
  Download and Podcasts folders from `/run/user/<uid>/gvfs/mtp:host=.../`. Needs GVfs in game mode
  (Bazzite) and the phone set to File transfer. Reading over MTP is slow: the first look at a phone takes a while.
- **Unplugged**: its source goes; if it was live, the source live before it is live again.
- Polled every 3 s (`DeviceWatcher`). Linux only.

## Audio CDs (`audiocd.py`, `cd.py`)

- **What**: an audio CD in a `/dev/sr*` drive, a source while it's in (`AudioCdService`, found by
  `devices.audio_cds`). Read with Linux's CD-ROM ioctls (`cd.py`: drive and disc status, the table of
  contents, `CDROMREADAUDIO` for raw frames), no program needed; the drive is the user's to read (uaccess).
- **Names**: the MusicBrainz disc ID (`cd.disc_id`, enhanced CDs' data session left out) looks up the
  release (`MusicBrainz.by_disc`), then its names for this disc and the Cover Art Archive's cover, kept as
  `cd-<disc id>.json` and `.jpg` in the runtime folder. Unknown discs play as "Track n".
- **Playing**: each track a WAV made on demand (`cd.TrackWav`), served by the stream server as a
  `StreamSource(reader=...)` with byte ranges, so seeking works.
- **Ripping**: the album page's "Rip to Local files" (`collection_action` "rip") writes
  `<artist>/<album>/<nn> <title>.flac` into the first Local files source's first folder: raw audio piped
  to ffmpeg's FLAC encoder, then tagged with mutagen (names, numbers, year, genres, MusicBrainz album id,
  cover). Progress in `status()["rip"]`, a toast when done. Without ffmpeg or a Local files source, it says so.

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
  starts or stops (`src/games.ts`). The backend keeps the last
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
- **Starred albums and artists**: `star`/`unstar` with `albumId` or `artistId`; an album's or artist's
  page knows from its own `starred` field.
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
- **Extras**: favourites for tracks, albums and artists alike (`/UserFavoriteItems`, with the pre-10.9
  path as fallback; a page knows from its item's `UserData.IsFavorite`), play reports
  (`/Sessions/Playing` then `/UserPlayedItems`), lyrics (`/Audio/{id}/Lyrics`, 10.9+), Instant Mix
  radio, playlist editing, loudness from `NormalizationGain`.
- Tested against `demo.jellyfin.org/stable` (12.2).

## Emby (`emby.py`)

- **What**: Jellyfin began as a fork of Emby and both still speak the same REST API, so
  `EmbyService` is `JellyfinService` with the differences below; everything else (header, audio,
  Instant Mix, mapping) is shared.
- **Differences**, found against Emby 4.10:
  - single items, favourites and "played" are under `/Users/{user}/Items/{id}`,
    `/Users/{user}/FavoriteItems/{id}` and `/Users/{user}/PlayedItems/{id}` (Jellyfin's newer
    paths answer 404);
  - `/Sessions/Playing` is refused (HTTP 400) without `MediaSourceId` and `PlaySessionId`; Jellyfin
    accepts them too, so the shared report always sends them;
  - playlist queries aren't filtered with `MediaTypes=Audio` (`PLAYLIST_FILTER`): Emby treats it as
    "or" and returns every item in the library;
  - no Quick Connect (username and password only, `SIGN_IN_HINT`);
  - `/Audio/{id}/Lyrics` errors, so lyrics come from the lyrics sites.
- **Art**: Emby only has art it found (a `cover.jpg`/`folder.jpg` next to the files, or embedded
  pictures); an album without either shows the placeholder, as it does in Emby's own apps.
- Tested against Emby Server 4.10.2 in a container, with a music library and a playlist.

## Plex (`plex.py`)

- **Protocol**: the Plex Media Server API its own apps use, JSON (`Accept: application/json`),
  with `X-Plex-Client-Identifier`/`Product`/`Platform` headers on every request (the transcoder
  refuses requests without a platform). MusicDeck appears under the server's Devices.
- **Sign-in** (`PlexLink`): a plex.tv PIN. In the dialog, a short code to enter at plex.tv/link
  on any signed-in device; "Sign in on this Deck" instead opens `app.plex.tv/auth/#!?…` with a long
  ("strong") code in Steam's browser. The token belongs to the PIN's client id, which is stored
  with it and reused when signing in again.
- **Finding the server** (`authenticate`): plex.tv's resources list gives the account's servers
  (owned first) with their addresses; all are tried at once and the best that answers wins (local,
  then remote, then relay; local ones also over plain HTTP, for routers that block `plex.direct`).
  A shared server uses its own access token. With more than one server the dialog shows a picker;
  a typed address overrides plex.tv's. Without a sign-in, a typed address is enough for a server
  that lets the network in (unclaimed, or "allowed without auth").
- **Browsing**: every music library (`type=artist` sections) merged. Plex filters: `>>=` means
  "more than", and paging needs both `X-Plex-Container-Start` and `-Size`. Search uses the
  libraries' `title` filter: `/hubs/search` is fuzzy enough to return a track for nonsense.
- **Audio**: the file part directly (byte ranges) when its codec plays in Chromium; otherwise
  `/music/:/transcode/universal/start` to MP3 320, one session id per stream. Transcoded streams
  have no byte ranges, so seeking only works within what has loaded.
- **Extras**: favourites (tracks, albums and artists) are star ratings (`/:/rate`: favouriting sets five stars, taking it back
  clears the rating; four stars or more shows as a favourite), play reports (`/:/timeline`, then
  `/:/scrobble`), lyrics (stream type 4: sidecar `.lrc`, or Plex Pass's provider, sent as timed
  lines), loudness from Plex's analysis (`gain`/`albumGain` on the audio stream), radio from sonic
  analysis (`/nearest`, Plex Pass) or else the same artist mixed with the library, playlists (smart
  ones are shown but can't be added to).
- **Ids**: rating keys (digits only); art ids are the server's image paths, served through
  `/photo/:/transcode` at 300 px.
- Tested against Plex Media Server 1.43 in a container (unclaimed, so without plex.tv), with
  sidecar lyrics; the plex.tv sign-in and server discovery against a fake plex.tv in the tests,
  and Plex's sign-in page opened on the Deck.

## SoulSync (`soulsync.py`)

- **What**: [SoulSync](https://github.com/nezreka/soulsync), the self-hosted server that fills a library
  from Soulseek, TIDAL, Qobuz and more (into Plex, Jellyfin, Navidrome, or its own Standalone library).
  MusicDeck plays the library of one the user already runs.
- **Sign-in**: its address and an API key (SoulSync: Settings > Advanced > SoulSync API), sent as
  `Authorization: Bearer sk_...`. It has to be an **admin** key: a read-only key only reaches `/api/v1`,
  and the audio isn't there. On save, `/api/v1/system/status` checks the key and `/stream/library-audio`
  without a path checks it may stream (400 for an admin key, 403 for a read-only one). Stored as `token`.
- **Browsing**: `/api/v1/library/` (recently-added albums and tracks, albums and artists in pages of 200,
  an album with its tracks, an artist's albums) and its search by name (artists, albums, songs by title and
  by artist). SoulSync allows 60 requests a minute from one address: answers are kept two minutes.
  Its playlists and Discover pool aren't in the library (Spotify's and iTunes' tracks), so they're not shown.
- **Audio**: `/stream/library-audio?path=<file_path>&track_id=<id>`, the route SoulSync's web player
  uses, with byte ranges; it serves only files in SoulSync's library folders (or proxies Navidrome's
  stream). Not part of the documented API: a SoulSync update could change it.
- **Art**: albums' `thumb_url`: a picture elsewhere on the web as it is, a path on the server with the key.
- **Ids**: `[\w.:-]`, checked before they reach a path.

## Audiobookshelf (`audiobookshelf.py`)

- **Protocol**: the REST API Audiobookshelf's own apps use (JSON, `Authorization: Bearer`).
- **Sign-in**: `POST /login` with `x-return-tokens: true` returns an access token (1 hour) and a
  refresh token (30 days, replaced on every renewal); servers before 2.26 return one token that
  doesn't expire. A refused access token is renewed once through `POST /auth/refresh`
  (`x-refresh-token` header) and the new pair saved; requests waiting on the same renewal share it.
  The stream server's requests don't pass through that path, so `stream`/`art` renew a token within
  5 minutes of expiring first and give the stream an `expires`, so a long book re-resolves.
  An API key (admins make them under Settings → API Keys) is accepted as the password with no
  username, checked with `/api/me`, and stored as the token with nothing to renew.
- **Model**: books and podcasts are "albums". A book's tracks are its audio files in order
  (`media.tracks`, each with `startOffset` in the book); with several files each is named after the
  chapter it starts, else its title tag, else "Part n". Episodes are a podcast's tracks, newest
  first. Ebook-only libraries are left out.
- **Progress** (feature `progress`): the server keeps it per book (time in the whole book) and per
  episode, shared with Audiobookshelf's apps. A file's position is its `startOffset` plus the
  player's position; `resume_position` reads the server fresh, so a book started on a phone
  resumes here. Files before the current place show as "Played", the current one as "n min left".
  Marking a file played moves the book to its end (finishing the book only at the last file);
  unplayed moves it back to the file's start. Within 60 s of the end, a book or episode is finished.
- **Chapters**: a file holding two or more chapters returns them from `lyrics` as synced lines
  titled "Chapters" (relative to the file), so the full-screen view jumps between them. Episodes
  and books never go to the lyrics sites.
- **Home**: Continue listening (episodes as they are; books as the file they were left in, up to
  8 fetched), then the server's own shelves under MusicDeck's names (new episodes, recently added,
  continue series, listen again, discover, recent series, authors; library named when there are
  several), then the libraries to browse in full.
- **Ids**: `b:`/`p:` + item, `a:` author, `s:<library>:<series>`, `l:` library, `c:` collection,
  `pl:` playlist; tracks `f:<item>:<inode>` and `e:<item>:<episode>`; art `i:<item>` (cover at
  600 px) and `a:<author>`. Every id part is checked against `[\w-]` before it goes into a path.
- **Formats**: MP3, AAC (M4B), FLAC, Opus and Vorbis play directly with byte ranges; anything
  else is reported as unplayable rather than transcoded (the server only transcodes whole books,
  as HLS).
- Tested against Audiobookshelf 2.37.1 in a container (multi-file and single-file M4B books with
  chapters, a series, a podcast), and in the tests against a fake server.

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
- **Audio**: yt-dlp. It first tries without a JavaScript engine, then with the bundled QuickJS-NG
  (`bin/<platform>/qjs`, checksum-pinned in `scripts/vendor.py`) when YouTube requires its signature challenge.
- **The worker**: both libraries run in `ytdlp_worker.py` on the system Python (Decky's Python
  lacks modules yt-dlp needs), each imported on first use. It holds nearly all the memory YouTube
  takes (about 80 MB with both loaded, measured on a Deck), so it starts on the first YouTube
  request and stops after 5 minutes without one; starting again takes about a second. YouTube
  Music calls are limited to the methods the plugin uses (`MUSIC_METHODS`).
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
  counts too) instead of the station's logo, place and genres. A song named without a cover gets one
  from Apple's catalogue (the iTunes Search API, no account; exactly that title by that artist, 600 px),
  remembered a day, "none" included. The media center's On air page shows it with the station's facts
  (`details`: language (two at most) and today's listeners, as text; its place and genres are under its name)
  and the songs heard earlier, as many as fit; stations like it are on Similar. `believable()` drops what isn't a song: the
  station's own name, playout codes ("PLAY_8_NEW", "ID1234"; a number alone can be a title), adverts,
  idents and web addresses. `tidy_on_air()` first drops a half that's the station's own (its name, its
  website): what's left counts only as "<song> by <artist>".
- **Jump back in**: the stations listened to (`played_station`, recorded once a station is playing;
  `recents.json` "stations", 12 per source), not the genre or country page they were found on.
- **Logos** come from wherever the directory points, and some hosts (DuckDuckGo's icon cache) refuse
  requests without a user agent, so `art()` sends MusicDeck's.
- **Favourites**: stored in this source's config (`favourites`) via `save_config`.

## Podcasts (`podcasts.py`, `feeds.py`)

- **Directory**: Apple's podcast directory, free and without an account: `itunes.apple.com/search`
  (shows and episodes), `/lookup` (an Apple id to its feed) and the top-charts JSON at
  `rss.marketingtools.apple.com` (country from the source's settings, US by default).
- **Feeds**: each show's own RSS feed, read by `musicdeck/feeds.py`, a small forgiving XML reader
  (Decky's Python has no `xml` package): elements, attributes, CDATA, comments, entities; namespaces
  stay as prefixes (`itunes:image`). Items without an audio enclosure are skipped. Feeds are cached
  for 30 minutes. A feed address pasted into Search opens that show.
- **Ids** carry what's needed to play, so nothing has to be looked up again after a restart: a show
  is `p:` + its feed URL in base64 (`a:` + an Apple id from the charts, looked up when opened), an
  episode `e:` + its audio URL. Art ids are image URLs.
- **Subscriptions** are in the source's config (`subscriptions`); the show page's Subscribe button
  is a Page `action` with `call` (see `collection_action`).
- **Progress**: the player reports an episode's position every 15 s and on pause
  (`save_progress`, feature `progress`); `stream_info` returns `start_at` (where it was left, 5 s
  back) so it resumes. Within 60 s of the end counts as played; "Mark as played/unplayed" in the
  track menu. Kept in `podcasts-<id>-progress.json`. Episodes resuming partway aren't preloaded for
  gapless playback.
- **Home**: Continue listening (episodes in progress), New episodes (the latest from each
  subscription), Your podcasts, Top podcasts.
- **Not done for episodes**: lyrics lookups and ListenBrainz reports (it's a music history).
- **Breaks when**: Apple moves the charts JSON (update `CHARTS`); a feed that's not RSS shows an
  error on its page.

## Live Music Archive (`livemusic.py`)

- **What**: the Internet Archive's `etree` collection, live recordings bands allow to be traded. Each
  band is an archive.org collection inside it; each show an item.
- **Lists and search**: `advancedsearch.php` (Solr): popular this week (`week desc`), just added,
  bands (`mediatype:collection`), a band's shows (newest first, the latest 300; the page says how
  many there are in all). "On this day" searches titles for the month and day, then keeps only shows
  whose date really is today's. Search sends only the words typed, as a phrase-free term list, so
  quotes or brackets can't change the query.
- **A show's tracks**: `/metadata/<item>`, files of the first format present in `FORMATS` (VBR MP3
  first; FLAC only when there's nothing else), ordered by file name (disc, then track).
- **Audio**: `/download/<item>/<file>`, which redirects to a storage server (proxied; the file name is
  quoted). Ids are checked so `..` can't climb out of the item.
- **Art**: archive.org's thumbnails; shows and tracks use their band's (a show's own is usually a
  generated waveform).
- **Radio**: tracks from three other popular shows by the same band, shuffled.
- **Breaks when**: archive.org changes advancedsearch's fields (`SEARCH`, the `fl[]` list).

## Qobuz (`qobuz.py`)

- **App credentials**: the web player's app id and signing secrets are read from its bundle
  (`BUNDLE_RE`, `APP_ID_RE`, `SEED_RE`), validated with a signed test request, cached a day in
  `qobuz-app.json`. This is how streamrip and qobuz-dl work.
- **Sign-in**: Qobuz's OAuth page (as Qobine does) with `redirect_url=http://localhost:<port>`; a
  one-shot loopback listener receives the code, exchanged via `oauth/callback` for a user token.
- **Audio**: `track/getFileUrl` (signed with the secret) at the chosen quality returns a direct
  FLAC/MP3 link. Plays are reported with `track/reportStreamingStart`, as the web player does.
- **Library**: albums and artists favourited with `favorite/create`/`favorite/delete` (`album_ids`,
  `artist_ids`), others' playlists subscribed with `playlist/subscribe`/`unsubscribe`. Whether a page's
  is in the library comes from the first 500 favourite albums and artists and playlists, asked once
  every five minutes; the user's own playlists get no button.
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
- **Extras**: favourites (one `favorites/ids` request marks hearts everywhere, and says which albums,
  artists and playlists are in My Collection; they're added through `favorites/albums`, `/artists` and
  `/playlists`, the user's own playlists aside), track radio, lyrics
  (synced LRC when available), home page modules (playlists, albums, mixes, tracks), playlist
  editing (with TIDAL's required `If-None-Match` ETag).
- Catalogue calls were tested with an app-only token; sign-in, playback, favourites and lyrics need
  a TIDAL subscription and were not yet tested end to end.

## Soulseek (`soulseek.py`, `slsk.py`)

- **Protocol**: `slsk.py` speaks Soulseek in pure Python (aioslsk, the Python library, needs pydantic's
  compiled core and an `xml.etree` UPnP library, neither of which Decky's Python can load), per
  Nicotine+'s documentation. One server connection (Login, SetWaitPort, SharedFoldersFiles 0/0,
  HaveNoParent, FileSearch, GetPeerAddress, ConnectToPeer); peer connections "P" (search results,
  QueueUpload, TransferRequest/Response) and "F" (a file), each opened directly or, when that can't work,
  by the other side after ConnectToPeer through the server (PierceFireWall with the asker's token).
  Listens on 2234 (the next free port up to 2253), and asks the router to forward it (`igd.py`: UPnP
  AddPortMapping, an hour at a time, renewed; given back at stop); without that, peers who can't be
  reached either aren't heard from.
- **Whole folders and libraries**: an album opens with FolderContents (36/37), so it has every track,
  not just those a search matched; "Everything <user> shares" is a browse (GetShareFileList 4/5).
- **Downloads** carry on: FileOffset is what's already in the `.part`; a file that fails is asked for
  again up to 3 times, 10 s and more apart; an album can be cancelled (the parts stay, to resume).
- **Sharing** (only with `share_dirs`): `Shares` indexes audio files (bitrate, length, sample rate, bit
  depth via mutagen; cached in `soulseek-<id>-shares.json`, rescanned hourly) as
  `MusicDeck\<folder>\...`; the server is told the true counts. Searches arrive from the server
  (FileSearch 26, EmbeddedMessage 93) and from a distributed parent (PossibleParents 102 → a D
  connection; DistribSearch 3; HaveNoParent 0, BranchLevel, BranchRoot sent once adopted; no children
  accepted), and are answered with up to 200 matches (every word, none of the -excluded). Browses and
  folder requests are answered from the index. QueueUpload for a shared file waits for one of 2 slots
  (PlaceInQueue answered); then TransferRequest, the peer's yes, an F connection, and the file from the
  offset it asks for. Anything else is UploadDenied ("File not shared.").
- **Untrusted peers**: messages over 16 MB refused, fields read with bounds checks, at most 5,000 files a
  response; only audio files up to 2 GB are fetched; a file's and folder's names are cleaned (`_safe`:
  no separators, no dots at the ends) before they become paths; a search result's user is the
  connection's, not what it claims. Only files in the shared folders are ever read for anyone.
- **Sign-in**: name and password; a new name is registered by signing in with it. The password is kept
  (`SECRET_KEYS`; never in backups): the protocol sends it at every sign-in and has no token.
- **Search**: answers gathered for 6 s; albums are one user's folder of two or more audio files, ranked
  by a free upload slot, queue, speed, lossless, bitrate; tracks likewise. Results are kept in memory to
  open and play.
- **Playing** downloads the file into `soulseek-<id>/` (1 GB, oldest let go first) and plays it from
  there; a user who hasn't started sending in 90 s is reported (with its place in their queue, when
  they say), and the download carries on.
- **Download album** (the page's action): every file asked for at once, saved in the chosen folder under
  the album's folder name; Home lists what's downloading and what's done (`soulseek-<id>.json`). Inside a
  local files source's folders, the album is noticed and gets ready as new music.

## Spotify (`spotify.py`, `backend/spotify-helper/`)

Modelled on [Spotifast](https://github.com/crmne/spotifast).

- **Two sign-ins in one flow** (OAuth with PKCE, redirect to `http://127.0.0.1:<port>/login`):
  1. *Playback*: Spotify's desktop client identity with the `streaming` scope. The access token is
     handed to the helper once; librespot turns it into reusable credentials, kept per account
     in `spotify/accounts/<username>/` (the sign-in runs in a scratch folder that becomes it).
  2. *Web API*: the public app shared by spotify-player and ncspot, or the user's own developer app
     (Client ID field), for library, search and changes. Only its refresh token is stored.
- **Helper** (`bin/<platform>/spotify-helper`, Rust; static musl builds for Linux, a Windows .exe): JSON-lines control on stdin/stdout;
  `GET /track/<id>` serves the decrypted original Ogg Vorbis file with byte ranges, `/info/<id>`
  returns Spotify's loudness values from the file header.
- **Session reads** (`reads.rs`): playlists, the playlist library (rootlist), albums, artists and
  lyrics are read over the librespot session (Spotify's own client endpoints), not the Web API, so
  they don't count against the shared app's rate limit.
- **Rate limiting** (`_Gate`): two requests in flight per app, a shared `Retry-After` cooldown, and
  fail-fast while it lasts; GETs cached for 3 minutes.
- **Library**: albums and other people's playlists saved, and artists followed, through `PUT`/`DELETE
  /me/library` with `spotify:` URIs (the February 2026 API's one endpoint for all of them);
  `/me/library/contains` says whether a page's is there, remembered for the session. Following artists
  needs the `user-follow-modify` scope, asked for since 0.17.0: a sign-in from before is told to sign in
  again when it tries.
- **Requirements**: Spotify Premium. Some newer accounts can't get audio keys through librespot
  ("audio key error"); that is a librespot/Spotify limitation.
- Build: `scripts/build_spotify_helper.sh` (cargo-zigbuild: x86_64 and arm64 musl, x86_64 Windows).

---

## Not possible

These were investigated and can't be supported without DRM circumvention:

- **Apple Music**: Widevine-only streams; Steam's browser engine has no CDM. Cider and Sidra are
  separate CastLabs Electron apps for this reason. The decky-apple-music plugin does the same
  inside Gaming Mode: a hidden CastLabs Electron player (313 MB installed) driven over an
  unauthenticated local DevTools port, with the Chromium sandbox off. Measured on the test Deck,
  idle and signed out: 4 processes, about 300 MB of memory (PSS), before any decoding. MusicDeck
  plays in Steam's own browser engine instead (its backend is about 60 MB), so it doesn't add a
  second browser.
- **Deezer, Amazon Music**: same situation.

## Lyrics sites (`lyrics.py`)

Asked for any source without lyrics of its own (or with none for the track); neither needs an
account. Both are asked at once; LRCLIB's synced lyrics win, then NetEase's, then LRCLIB's unsynced.

- **LRCLIB** (lrclib.net): `/api/get` (exact title, artist, album, ±2 s), then `/api/search`. One
  retry after a server error or dropped connection; not after 429 (rate limited).
- **NetEase Cloud Music**: `/api/search/get`, then `/api/song/lyric` (as the
  [syncedlyrics](https://github.com/moehmeni/syncedlyrics) library does). NetEase lists a song many
  times, so matches (title, artist, length within 3 s; not instrumental/karaoke) are ranked and the
  best three tried. Credit lines are removed (`作词 : …` anywhere; `Label: value` stamped in the
  first second). `pureMusic` means instrumental: no lyrics. Asked **without cookies**: once its
  `NMTID` cookie comes back, NetEase cuts results to 10 and the right versions drop out (the shared
  session uses a `DummyCookieJar`).
- No lyrics is only reported when every site said so; one that couldn't be asked makes it an
  error, which the UI doesn't remember, so the next look tries again.
- Musixmatch was considered and left out: its guest token came back blank when tried.

