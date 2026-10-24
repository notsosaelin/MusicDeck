# MusicDeck

A [Decky Loader](https://decky.xyz) plugin for SteamOS, Bazzite and other SteamOS-like systems. It turns the
**Soundtracks** tab of the Quick Access Menu into a music player that keeps playing while you're in a game.

## Features

- **Eighteen sources:** local files, your Steam soundtracks, Navidrome/Subsonic, Jellyfin, Emby, Plex,
  Audiobookshelf, SoulSync, SoundCloud, YouTube Music (plus regular YouTube), Bandcamp, internet radio, podcasts, the Live
  Music Archive, Qobuz, TIDAL, Spotify and Soulseek. One source is live at a time, chosen in the plugin's Decky page.
- **Steam soundtracks, full circle:** the soundtracks you own on Steam, in the tab Steam made for them. The
  game you're playing puts its soundtrack first, and soundtracks that aren't installed install from there.
- **Games:** pick an album or playlist for a game and it plays when the game starts; or have the music
  pause while you play and pick up again when the game closes.
- **Several accounts:** add a service more than once (two Spotify accounts, a second Navidrome user) and
  switch between them like any other source.
- **Native Quick Access tab:** Now playing, Library, Search and Settings, with a mini player while you
  browse, built from Steam's own components and controller conventions. Now playing is two pages: the
  player, then below it the song's lyrics (when there are none, the cover as a turning record with the music round it) or the queue. Settings
  has what the media center's has: the equalizer, playback, scrobbling, games and the media center's own
  choices (sources are picked in Decky → MusicDeck).
- **Your music library:** local files as a library, not a folder list: albums (compilations kept whole, discs
  in CD1/CD2 folders together), artists with the songs they appear on elsewhere, genres, years by decade and
  composers, all from the files' tags. MusicDeck counts plays on the Deck for Recently played, Most played and
  Not played yet; star ratings in the files make Top rated. Now Playing's Info shows the file's format
  (FLAC 24-bit · 96 kHz, MP3 · 320 kbps), year, genre, composer, label, rating and plays.
- **Library manager for your files:** edit a track's or a whole album's tags (**Y** → Edit tags; the last
  edit to each file can be undone), fill them in from a [MusicBrainz](https://musicbrainz.org) release you
  pick after seeing exactly what would change (MusicBrainz, and Apple Music's and Deezer's public
  catalogues for what it doesn't know), and give albums and artists pictures: one of your own, an
  album's cover from the Cover Art Archive (then Apple's or Deezer's, up to 1200 px), an artist's picture from Deezer's public catalogue (no account),
  or every missing one at once (Decky → MusicDeck → the source's menu → Find missing artwork). Pictures are
  kept in MusicDeck. Lyrics found online can be kept for a track, an album, or the whole library (Find
  missing lyrics), synced when they are: in MusicDeck, or as the same-named `.lrc` beside each song that
  Navidrome, Jellyfin, Plex and foobar2000 read too.
- **New music gets ready by itself:** albums copied into your folders are noticed as they arrive (and
  checked for hourly, and at start-up). Each new album gets its cover and artist picture; MusicBrainz tags
  are **Ask me** by default (new albums it knows wait under Browse → To review, and nothing is written
  until you look), or **Only when sure** (an unmistakable match fills in only empty fields), or off; and
  lyrics if you want them. A note says what each batch got. Your music folders only change when you edit
  tags, or choose to keep lyrics beside the songs.
- **Media center:** the player full screen in Steam's main window, laid out like Steam's library, with the
  same five tabs for every source: Now Playing, Home, Browse, Search and Settings. Browse holds what each
  source has besides its Home, every tile saying what's in it with covers from it: your own music as
  large tiles (Albums, Artists, Playlists, Favourites or Liked Songs, Songs, your stations or podcasts,
  and the playlists made in MusicDeck); For you (a server's Recently added, Recently played, Most played
  and Random; local files' Recently played, Most played, Not played yet and Top rated; Spotify's Your
  top) and the ways in (YouTube Music's New and Charts; Qobuz's Playlists by Qobuz, Press awards, Best
  sellers and more; Audiobookshelf's Authors and Series...) as rows of their first items ending in a square
  Show all; genres, moods, countries and languages as a row each (Pop/Rock's albums, then Jazz's...); and
  local Folders with how much space the library takes. Grids show whole rows that fit the screen, covers filling the screen edge
  to edge, and every page scrolls a whole row at a time. Settings has the equalizer; what it opens on, background, cover
  and lyrics sizes, screen off when idle; and playback, scrobbling and games, as in Decky. All on the
  controller.
- **Now Playing for what's playing:** songs show their lyrics and the queue (local files their Info too); a
  station its logo as big as its square, and an On air page: the song and its cover when the station says
  (the cover looked up when it doesn't send one); its language and today's listeners; and what it
  played earlier (only what looks like songs: a station's own name or its
  playout codes aren't); stations like it under Similar, and the stations you listened to in Jump back in; a podcast episode its show notes and the
  show's other episodes, with skip and speed controls; a book its chapters and blurb; a concert from the
  Live Music Archive its setlist and recording notes.
- **Screen off:** in the media center, **Y** darkens the screen while the music plays; any button wakes it.
- **Cast to speakers and TVs:** Chromecast, Google/Nest speakers and anything with Google Cast, plus UPnP/DLNA
  devices (Sonos, AV receivers, smart TVs). The speaker fetches the music itself; the queue, play reports and
  controls stay on the Deck.
- **Jump back in:** each source's home starts with the albums and playlists you played last, and an empty
  search shows your recent searches.
- **Your library on the service:** an album's page has **Add to library** (Spotify, Qobuz, TIDAL) or **Add to
  favourites** (Navidrome, Jellyfin, Emby, Plex), an artist's **Follow** or **Add to favourites**, and someone
  else's playlist on Spotify, Qobuz or TIDAL **Add to library**, in the Quick Access panel and the media
  center. What you add shows in the source's Albums, Artists, Playlists and Favourites at once, there and in
  the service's own apps.
- **Playlists:** Navidrome, Jellyfin, Emby, Plex, Qobuz, Spotify and TIDAL keep yours on the service: add
  to them, or make a new one from any track. Every other source gets **MusicDeck playlists**, stored on the
  device, with custom artwork. Save any playlist or album from a source as your own.
- **Downloads:** download a MusicDeck playlist and it plays with no connection (and no data used), from
  your own servers, SoundCloud, YouTube, Bandcamp, podcasts and the Live Music Archive. Each song is kept
  once however many playlists hold it, and goes when the last one lets it go.
- **Plug in a music player:** an MP3 player, an iPod in disk mode, a USB stick or a phone set to "File
  transfer" shows up in Decky → MusicDeck as a source while it's plugged in, as on the Xbox: pick it to
  browse and play what's on it. MusicDeck only reads it (no tag edits). Phones need a system with GVfs in
  game mode (Bazzite has it); on others, USB storage only.
- **Likes:** **Y** on a song likes it in the source's own words: Save to Liked Songs on Spotify, Add to My
  Collection on TIDAL, Add to favourites on Qobuz and your servers. SoundCloud, YouTube Music, Bandcamp and the
  Live Music Archive keep likes on the Deck, listed under Liked songs (Browse, and the panel's Library).
- **Synced lyrics:** the source's own lyrics, otherwise [LRCLIB](https://lrclib.net) with NetEase Cloud Music as a
  backup, following the song under Now playing or full screen over the album art.
- **Sound:** 10-band EQ with presets (one for everything, or one each for the speakers, headphones and
  each Bluetooth device or TV, switched as you change output), volume levelling from ReplayGain data,
  gapless playback, an optional crossfade, music played quieter or paused in games, and endless radio
  when the queue runs out.
- **Live radio** with the song on air shown as it plays, stations browsed as stations (each played on its
  own), lists of your own stations to move through with previous and next, and controls made for radio
  (favourite, previous and next station, play or stop, add to a list).
- **ListenBrainz and Last.fm scrobbling** for every source, including plays made offline. Last.fm needs an
  API account of your own (free, at last.fm/api/account/create); you then approve MusicDeck on Last.fm's
  own page. If your Navidrome server already sends plays to Last.fm, link it in one place only, or plays
  count twice.
- **Backup and restore:** your sources, playlists (with their pictures), favourites and settings in one
  file, for a new Deck or a fresh start. Backups never hold sign-ins, so they're safe to copy anywhere.
- **Sleep timer:** pause after 15–90 minutes with a fade-out, or at the end of the track.
- **Picks up where you left off:** the queue survives restarts, and with Navidrome it follows you between
  devices.

## Sources

| Source | Account | What you get |
| --- | --- | --- |
| Local files | — | Recursive scan; albums, artists, genres, years, composers and folders from the tags; artwork, `.lrc`/embedded lyrics, ReplayGain, `.m3u` playlists, favourites, star ratings, play counts kept on the Deck, format and quality, radio; tag editing, MusicBrainz look-ups, custom and found artwork |
| Steam soundtracks | Your Steam account | Every soundtrack you own, from every Steam library (SD cards too); the running game's soundtrack first; install the rest with Steam's own dialog; one copy when a soundtrack ships MP3 and FLAC; composers, favourites, lyrics, radio |
| Navidrome / Subsonic | Your server | Favourites, play reports, lyrics, similar-song radio, queue sync across devices, add to server playlists, ReplayGain |
| Jellyfin | Your server | Sign in with Quick Connect (no typing), favourites, play reports, lyrics, Instant Mix radio, add to playlists, original-quality audio |
| Emby | Your server | Favourites, play reports, Instant Mix radio, add to playlists, original-quality audio |
| Plex | Your server (Plex account, or none for a server open to your network) | Sign in with a plex.tv/link code, server picker, favourites (star ratings), play reports, lyrics, radio, add to playlists, loudness levelling, original-quality audio |
| Audiobookshelf | Your server | Audiobooks and podcasts: continue listening, series, authors, collections and playlists, search; books resume where any of your Audiobookshelf apps left them, chapters to jump between, mark as played |
| SoulSync | Your server (an admin API key) | The library of the SoulSync you run: recently added albums and songs, albums, artists, search; plays its files, seeking included |
| SoundCloud | None | Search, curated selections, artists, playlists and albums, related-track radio |
| YouTube Music | None | Home, search across YouTube Music **and** regular YouTube, artists and channels, radio, lyrics |
| Bandcamp | None | Genre charts, search, albums and artist discographies, full-track streaming, lyrics, same-artist radio |
| Internet radio | None | Tens of thousands of stations from [Radio Browser](https://www.radio-browser.info): popular near you, trending, genres, search, favourite stations, song on air |
| Podcasts | None | Apple's top charts, search for shows and episodes (or paste a feed address), subscriptions, new episodes, episodes resume where you stopped, mark as played |
| Live Music Archive | None | Over 250,000 concert recordings bands allow to be shared ([archive.org](https://archive.org/details/etree)): concerts played on this day, popular this week, bands and their shows, search, same-band radio |
| Qobuz | Your subscription | Your playlists and favourites (synced), editorial picks, search, MP3 up to 24-bit/192 kHz FLAC, plays reported as from the official player |
| TIDAL | Your subscription | Your playlists, mixes and favourites, TIDAL's home page, search, track radio, synced lyrics, up to CD-quality FLAC |
| Soulseek | A Soulseek name (registered when you first sign in with it) | Search what other people share, albums by folder (the whole folder) with format and quality, browse a user's whole library, play a track (downloaded first), download an album into your music folder (default `~/Music/Soulseek`), where your local files pick it up and get it ready; downloads carry on after interruptions, retry and can be cancelled; optionally share folders of your own (searches, browsing, uploads two at a time), with your router asked to forward the port |
| Spotify | Premium | Your playlists, Liked Songs (synced), recently played, top tracks and artists, saved albums, new releases, search, Spotify's synced lyrics, up to 320 kbps, Spotify's own loudness levelling |

**No accounts where a service doesn't need one.** SoundCloud, YouTube, Bandcamp, radio, podcasts and the Live Music Archive are used signed
out, as NewPipe does: nothing ties your listening to an identity, and there's no account to get flagged for
using an unofficial client. Your playlists and favourites for those stay on the Deck.

**Passwords are never stored**, except Soulseek's: its protocol signs in with the password every time and
has no tokens, so it's kept like the tokens below (and never in backups). Navidrome passwords become Subsonic tokens and Jellyfin, Emby and
Audiobookshelf passwords become access tokens on save (or skip the password with Jellyfin's Quick Connect).
Plex signs in with a code entered at plex.tv/link. Qobuz, TIDAL and Spotify sign in on their own websites, in
Steam's browser, and MusicDeck only receives the resulting tokens.

**Where those tokens live.** In MusicDeck's settings file, which only your user account can read (mode 600, in a private folder),
and not encrypted: SteamOS's game mode has no system keyring to lock them with, and a key kept beside them
would protect nothing. Anything running as your user, or someone signed in to your Deck (SSH included), can
read them, as with every other app's saved sign-ins. They never reach the Steam UI, and backups leave them out.
Removing a source deletes its tokens; signing out on the service's website revokes them.

**English only.** The interface isn't translated yet; text from the sources (titles, lyrics) shows as they send it.

**Spotify and following artists:** Spotify sources signed in before 0.17.0 can save albums and playlists
but not follow artists; edit the source and sign in again to allow it.

**Spotify tip:** Spotify's public Web API app is shared by every client that uses it and is often
rate-limited. Most of MusicDeck's Spotify browsing avoids it, but search and your listening history use it.
Add your own free app's Client ID in the Spotify source (the dialog links to Spotify's developer dashboard).

## Using it

| Where | What |
| --- | --- |
| Decky → MusicDeck | Add and manage sources, pick the live one, playback options, ListenBrainz and Last.fm, debug log |
| Quick Access → Soundtracks tab | The player. Takes over the tab by default, or gets its own tab |
| The expand button on the panel's tab strip (or the lyrics) | The media center, full screen |

Controller: **A** plays or opens, **X** adds a track to the queue (removes it, in the queue) or plays an album or
playlist without opening it, **Y** opens a track's or playlist's options (including the sleep timer for the
playing track), **B** goes back, **L1**/**R1** switch tabs. Re-selecting Library or Search returns to its start.

On Now playing, down from the player is its second page: the lyrics, or the queue (switched at the page's
top). The queue lists tracks in the order they'll play (shuffled or not); **Clear** at its top empties it
(after asking); **Y** on a queued track moves it (play next, up, down, to the end), and **Move** picks it up so
the d-pad carries it until **A** or **B** puts it down.

Switching the live source stops playback and clears the queue; nothing from the previous source stays queued.

**Media center:** **L1**/**R1** switch tabs (Now Playing, Home, Browse, Search, Settings); **A** opens a Browse tile, an
album or playlist, **X** plays it straight away, **B** goes back a page, then up to the tabs, then back to the
Quick Access panel; **View** goes back to the panel at once; **Start** plays or pauses from anywhere. On Now
Playing, **R2** goes through what's beside the cover (lyrics, queue, on air, notes...), and **Y** turns the screen off (Settings can do it
after a few idle minutes). Handhelds only switch the backlight fully off for root, so "off" is Steam's
lowest brightness under a black screen: nearly dark, on any device.

**Casting:** **X** on the volume slider lists the speakers and TVs on your network (and This Deck). The
volume slider then sets the device's volume and shows its name. The equalizer, volume levelling and
crossfade only apply on the Deck. Spotify can't be cast from MusicDeck (use Spotify Connect); TVs and
receivers get CD quality from Qobuz, and AAC rather than Opus from YouTube. UPnP renderers can't play
HLS streams (some radio stations, SoundCloud, TIDAL).

**Game music:** with a game running, open an album or playlist in MusicDeck and press the gamepad button
next to Play (or choose "Play when … starts" from a row's options). From then on it plays whenever that
game starts, and pauses when the game closes unless you've picked something else meanwhile. Each source
keeps its own choices and only the live source's apply; Decky → MusicDeck → Games lists them all.

**In games** (Decky → MusicDeck → Games) sets what the music does while you play: keep playing as it is
(the default), play quieter while a game is in front (a little, by half or a lot; it comes back up in
Steam's menus, and your volume setting stays as it is), or pause when a game starts and resume when the
last game closes, unless you played, skipped or changed something meanwhile. Music played on a speaker
or TV isn't lowered.

Any service can be added more than once, one account each; MusicDeck refuses the same account twice.
For a second account of a service that signs in on its website, sign out on that website in Steam's
browser first (Spotify's page also offers "Not you?").

## Install

Until it's in the Decky store: enable Decky's developer mode, then **Decky → Settings → Developer → Install
plugin from ZIP** with the zip for your system (its SHA-256 is in the `.sha256` file beside it):

| Zip | For |
| --- | --- |
| `MusicDeck-<version>-linux-x86_64.zip` | Steam Deck, SteamOS, Bazzite, ChimeraOS and other x86_64 Linux |
| `MusicDeck-<version>-linux-arm64.zip` | Armada, DroidDeck, Steam Frame and other ARM64 Linux |
| `MusicDeck-<version>-windows-x86_64.zip` | Windows, on x86_64 or Arm |

What changed in each version is in [CHANGELOG.md](CHANGELOG.md).

### Platforms

| Where | Decky | MusicDeck |
| --- | --- | --- |
| SteamOS and SteamOS-like Linux on x86_64 (Steam Deck, Bazzite, ChimeraOS...) | Official | Supported: tested on a device for every release |
| ARM64 Linux: [Armada](https://github.com/armada-os/armada) on ARM handhelds, [DroidDeck](https://github.com/Droid-Deck/DroidDeck) on Android, Steam Frame | Decky 3.3 or newer (Armada and DroidDeck ship it) | Experimental: ARM64 builds of its helpers ship in the zip |
| Windows (x86_64, and Arm through Windows' emulation) | Decky 3.3's experimental Windows build, e.g. installed by [Playhub](https://github.com/LoZazaMastro/Playhub) | Experimental |

On Windows, some things work differently: YouTube runs in the Python that comes with MusicDeck's Windows
zip (python.org's embeddable Python; nothing to install); one equalizer for every output (Windows doesn't say which output is in use the
way PipeWire does); new music in your folders is found by the hourly check rather than at once; screen off
and lowering the music in games depend on Steam's game mode, which Windows doesn't have; the Steam
soundtracks source finds Steam where Windows' registry says it is. Decky's team doesn't support Windows: ask
Playhub's community, or here.

Logs are in `~/homebrew/logs/MusicDeck/`. Turn on **Debug log** under Troubleshooting in the plugin settings
for a detailed playback timeline in `musicdeck-debug.log`.

## Documentation

| Document | For |
| --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How it works: processes, the life of a play, every module, storage, security |
| [docs/SOURCES.md](docs/SOURCES.md) | Technical notes per source: protocols, sign-in, streams, what breaks |
| [docs/ADDING_A_SOURCE.md](docs/ADDING_A_SOURCE.md) | Step-by-step guide and checklist for a new source |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Building, testing, deploying to a device, debugging, conventions |
| [docs/SECURITY.md](docs/SECURITY.md) | Threat model, every entry point and its guard, audit findings, remaining risk |

## Roadmap

Not possible:

- **Apple Music, Deezer, Amazon Music.** Their catalogues are only served as DRM-protected (Widevine)
  streams, and Steam's browser engine has no DRM system, which a plugin can't add. Desktop clients such as
  [Cider](https://github.com/ciderapp/cider) and [Sidra](https://github.com/wimpysworld/sidra) are separate
  apps built on CastLabs Electron, a Chromium that ships Widevine; nothing comparable exists inside Steam.
  MusicDeck doesn't decrypt streams.
- **Nintendo Music.** Its web player (music.nintendo.com) streams Widevine-protected DASH too, and
  Nintendo's terms rule out other apps using it.
- **Downloads from Qobuz, TIDAL and Spotify.** Offline listening is part of what those subscriptions license
  to their own apps, under DRM (Spotify) or terms that rule out other apps keeping the files; MusicDeck
  streams them only.
- **Some SoundCloud tracks.** A few label releases are only streamed DRM-protected, for the same
  reason; MusicDeck leaves them out of lists, along with Go+ tracks free accounts only hear 30 seconds of.
- **TIDAL 24-bit "Max".** TIDAL only grants it to a sign-in that hands its result back through a browser
  redirect Steam doesn't expose to plugins; MusicDeck plays TIDAL up to CD quality.

## Thanks

Built on the work of [librespot](https://github.com/librespot-org/librespot) and
[Spotifast](https://github.com/crmne/spotifast), [yt-dlp](https://github.com/yt-dlp/yt-dlp),
[ytmusicapi](https://github.com/sigma67/ytmusicapi), [python-tidal](https://github.com/EbbLabs/python-tidal),
[streamrip](https://github.com/nathom/streamrip), [Qobine](https://github.com/sofusa/qobine),
[NewPipe](https://github.com/TeamNewPipe/NewPipe), [Radio Browser](https://www.radio-browser.info),
[LRCLIB](https://lrclib.net), [syncedlyrics](https://github.com/moehmeni/syncedlyrics) and [hls.js](https://github.com/video-dev/hls.js).

## License

GPL-3.0-or-later. Bundled: mutagen (GPL-2.0+), yt-dlp and yt-dlp-ejs (Unlicense), ytmusicapi (MIT),
requests (Apache-2.0), QuickJS-NG (MIT), hls.js (Apache-2.0), librespot (MIT, in spotify-helper).
