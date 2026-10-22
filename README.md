# MusicDeck

A [Decky Loader](https://decky.xyz) plugin for SteamOS, Bazzite and other SteamOS-like systems. It turns the
**Soundtracks** tab of the Quick Access Menu into a music player that keeps playing while you're in a game.

## Features

- **Sixteen sources:** local files, your Steam soundtracks, Navidrome/Subsonic, Jellyfin, Emby, Plex,
  Audiobookshelf, SoundCloud, YouTube Music (plus regular YouTube), Bandcamp, internet radio, podcasts, the Live
  Music Archive, Qobuz, TIDAL and Spotify. One source is live at a time, chosen in the plugin's Decky page.
- **Steam soundtracks, full circle:** the soundtracks you own on Steam, in the tab Steam made for them. The
  game you're playing puts its soundtrack first, and soundtracks that aren't installed install from there.
- **Games:** pick an album or playlist for a game and it plays when the game starts; or have the music
  pause while you play and pick up again when the game closes.
- **Several accounts:** add a service more than once (two Spotify accounts, a second Navidrome user) and
  switch between them like any other source.
- **Native Quick Access tab:** Now playing (with lyrics), Library, Search, Queue and Equalizer, with a mini player while you browse, built from Steam's
  own components and controller conventions.
- **Jump back in:** each source's home starts with the albums and playlists you played last, and an empty
  search shows your recent searches.
- **MusicDeck playlists** for every source, stored on the device, with custom artwork. Save any playlist or
  album from a source as your own.
- **Downloads:** download a MusicDeck playlist and it plays with no connection (and no data used), from
  your own servers, SoundCloud, YouTube, Bandcamp, podcasts and the Live Music Archive. Each song is kept
  once however many playlists hold it, and goes when the last one lets it go.
- **Synced lyrics:** the source's own lyrics, otherwise [LRCLIB](https://lrclib.net) with NetEase Cloud Music as a
  backup, following the song under Now playing or full screen over the album art.
- **Sound:** 10-band EQ with presets, volume levelling from ReplayGain data, gapless playback, an
  optional crossfade, and endless radio when the queue runs out.
- **Live radio** with the song on air shown as it plays.
- **ListenBrainz scrobbling** for every source, including plays made offline.
- **Backup and restore:** your sources, playlists (with their pictures), favourites and settings in one
  file, for a new Deck or a fresh start. Backups never hold sign-ins, so they're safe to copy anywhere.
- **Sleep timer:** pause after 15–90 minutes with a fade-out, or at the end of the track.
- **Picks up where you left off:** the queue survives restarts, and with Navidrome it follows you between
  devices.

## Sources

| Source | Account | What you get |
| --- | --- | --- |
| Local files | — | Recursive scan, tags and artwork, `.lrc`/embedded lyrics, ReplayGain, `.m3u` playlists, favourites, radio |
| Steam soundtracks | Your Steam account | Every soundtrack you own, from every Steam library (SD cards too); the running game's soundtrack first; install the rest with Steam's own dialog; one copy when a soundtrack ships MP3 and FLAC; composers, favourites, lyrics, radio |
| Navidrome / Subsonic | Your server | Favourites, play reports, lyrics, similar-song radio, queue sync across devices, add to server playlists, ReplayGain |
| Jellyfin | Your server | Sign in with Quick Connect (no typing), favourites, play reports, lyrics, Instant Mix radio, add to playlists, original-quality audio |
| Emby | Your server | Favourites, play reports, Instant Mix radio, add to playlists, original-quality audio |
| Plex | Your server (Plex account, or none for a server open to your network) | Sign in with a plex.tv/link code, server picker, favourites (star ratings), play reports, lyrics, radio, add to playlists, loudness levelling, original-quality audio |
| Audiobookshelf | Your server | Audiobooks and podcasts: continue listening, series, authors, collections and playlists, search; books resume where any of your Audiobookshelf apps left them, chapters to jump between, mark as played |
| SoundCloud | None | Search, curated selections, artists, playlists and albums, related-track radio |
| YouTube Music | None | Home, search across YouTube Music **and** regular YouTube, artists and channels, radio, lyrics |
| Bandcamp | None | Genre charts, search, albums and artist discographies, full-track streaming, lyrics, same-artist radio |
| Internet radio | None | Tens of thousands of stations from [Radio Browser](https://www.radio-browser.info): popular near you, trending, genres, search, favourite stations, song on air |
| Podcasts | None | Apple's top charts, search for shows and episodes (or paste a feed address), subscriptions, new episodes, episodes resume where you stopped, mark as played |
| Live Music Archive | None | Over 250,000 concert recordings bands allow to be shared ([archive.org](https://archive.org/details/etree)): concerts played on this day, popular this week, bands and their shows, search, same-band radio |
| Qobuz | Your subscription | Your playlists and favourites (synced), editorial picks, search, MP3 up to 24-bit/192 kHz FLAC, plays reported as from the official player |
| TIDAL | Your subscription | Your playlists, mixes and favourites, TIDAL's home page, search, track radio, synced lyrics, up to CD-quality FLAC |
| Spotify | Premium | Your playlists, Liked Songs (synced), recently played, top tracks and artists, saved albums, new releases, search, Spotify's synced lyrics, up to 320 kbps, Spotify's own loudness levelling |

**No accounts where a service doesn't need one.** SoundCloud, YouTube, Bandcamp, radio, podcasts and the Live Music Archive are used signed
out, as NewPipe does: nothing ties your listening to an identity, and there's no account to get flagged for
using an unofficial client. Your playlists and favourites for those stay on the Deck.

**Passwords are never stored.** Navidrome passwords become Subsonic tokens and Jellyfin, Emby and
Audiobookshelf passwords become access tokens on save (or skip the password with Jellyfin's Quick Connect).
Plex signs in with a code entered at plex.tv/link. Qobuz, TIDAL and Spotify sign in on their own websites, in
Steam's browser, and MusicDeck only receives the resulting tokens.

**Where those tokens live.** In MusicDeck's settings file, which only your user account can read (mode 600, in a private folder),
and not encrypted: SteamOS's game mode has no system keyring to lock them with, and a key kept beside them
would protect nothing. Anything running as your user, or someone signed in to your Deck (SSH included), can
read them, as with every other app's saved sign-ins. They never reach the Steam UI, and backups leave them out.
Removing a source deletes its tokens; signing out on the service's website revokes them.

**English only.** The interface isn't translated yet; text from the sources (titles, lyrics) shows as they send it.

**Spotify tip:** Spotify's public Web API app is shared by every client that uses it and is often
rate-limited. Most of MusicDeck's Spotify browsing avoids it, but search and your listening history use it.
Add your own free app's Client ID in the Spotify source (the dialog links to Spotify's developer dashboard).

## Using it

| Where | What |
| --- | --- |
| Decky → MusicDeck | Add and manage sources, pick the live one, playback options, ListenBrainz, debug log |
| Quick Access → Soundtracks tab | The player. Takes over the tab by default, or gets its own tab |

Controller: **A** plays or opens, **X** adds a track to the queue (removes it, in the Queue) or plays an album or
playlist without opening it, **Y** opens a
track's or playlist's options (including the sleep timer for the playing track), **B** goes back, **L1**/**R1**
switch tabs.
Re-selecting Library or Search returns to its start. The Queue lists tracks in the order they'll play
(shuffled or not); **X** on the Queue tab clears it (after asking); **Y** on a queued track moves it (play next, up, down, to the end), and **Move…** picks it
up so the d-pad carries it until **A** or **B** puts it down.

Switching the live source stops playback and clears the queue; nothing from the previous source stays queued.

**Game music:** with a game running, open an album or playlist in MusicDeck and press the gamepad button
next to Play (or choose "Play when … starts" from a row's options). From then on it plays whenever that
game starts, and pauses when the game closes unless you've picked something else meanwhile. Each source
keeps its own choices and only the live source's apply; Decky → MusicDeck → Games lists them all.

**Pause in games** (Decky → MusicDeck → Games, off by default) pauses the music when a game starts
and resumes it when the last game closes, unless you played, skipped or changed something meanwhile.

Any service can be added more than once, one account each; MusicDeck refuses the same account twice.
For a second account of a service that signs in on its website, sign out on that website in Steam's
browser first (Spotify's page also offers "Not you?").

## Install

Until it's in the Decky store: enable Decky's developer mode, then **Decky → Settings → Developer → Install
plugin from ZIP** with `MusicDeck.zip`.

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
