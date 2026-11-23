# MusicDeck

A [Decky Loader](https://decky.xyz) plugin for SteamOS, Bazzite and other SteamOS-like systems. It turns the
**Soundtracks** tab of the Quick Access Menu into a music player that keeps playing while you're in a game.

## Features

- **Eighteen sources:** local files, your Steam soundtracks, Navidrome/Subsonic, Jellyfin, Emby, Plex,
  Audiobookshelf, SoulSync, SoundCloud, YouTube Music (plus regular YouTube), Bandcamp, internet radio, podcasts, the Live
  Music Archive, Qobuz, TIDAL, Spotify and Soulseek.
- **Your devices:** MusicDeck on your PC and your Deck, paired once (Settings → Your devices: a code shows on both,
  accept it on the other), keep up with each other: each one's listening counts in the other's stats, and **Continue
  listening** on Home picks up what the other was playing, from where it was (the other stops), and **Play on**
  (the cast button) lists the other devices that are on: the music plays there as on a speaker, this one showing
  where it's got to and controlling it, and This Deck brings it back. **Playlists** are the same on all of them:
  one made or changed on any shows on the others within seconds. Songs from a
  streaming service play from the same service here; songs from Local files stream from the device they're on. Once
  paired, they find each other **anywhere**, not just at home (another network, a hotspot), with nothing to set up:
  what each plays, Continue, Play on, playlists and listens all work across the internet (Local files and discs stay
  home). On Windows, Settings first offers **Allow device connections** (Windows' firewall asks for an administrator's
  yes, once).
- **One library:** every source's music together, as Steam's library is every game whatever store it came from.
  Playlists mix sources (a Qobuz song, a SoundCloud one and your own files in one list).
  Home has what you played lately from all of them, then each source in turn: a row of its albums or playlists
  (the rows take turns) and a list of its songs; **Your music** (Playlists, Albums, Artists, Songs) puts every
  source's together, a row of each in the media center, each card marked with its source's logo (the same album
  saved from three services is three cards you can tell apart), filtered to one source or ordered by title,
  artist or source; **Search** asks every source at once, each one's results showing as they come. A queue can mix sources.
  Each source's own parts (Qobuz's picks, Local files' folders and tools) stay under its name. On desktop Steam
  and in the media center; the Quick Access panel still browses the live source.
- **Desktop Steam too:** a **Music** tab beside Store, Library and Community, as if Steam always had one.
  Your library on the left and its pages on the right, laid out as Steam's library is (an album headed by its
  art with a green Play), track lists with columns to sort, Ctrl/Shift to pick several, right-click for
  Steam's own menus, and a player along the bottom laid out as Spotify's is (the song, the controls and its
  place, then lyrics, queue, casting, volume; the equalizer and settings under **...**). Click the cover for a
  big Now Playing: the cover over a blur of itself, beside its synced lyrics (what's up next for a song without), what's up next, or alone, with
  a visualizer. Leave Music while it plays and a mini player window takes over: dragged anywhere, sized at
  will in three shapes it settles into so the cover always shows whole (a square the cover fills, the
  controls over it on hover; a card with the song below the cover; or a strip), with casting and its volume
  a click away, its window's options (Open Music, keep on top, close) on right-click, and opened by hand from
  the player too. Dialogs resize like any window. Also on the
  desktop: smart playlists (Local files' songs picked by rules, as iTunes's are), Get Info for several songs
  at once, dragging songs onto a playlist or a device in the sidebar (and music files from your desktop into
  Local files), a device's capacity at a glance, keyboard shortcuts, media keys and the system's media
  controls, and Play soundtrack on the page of a game whose soundtrack you own. Windows and Linux desktops.
- **Steam soundtracks, full circle:** the soundtracks you own on Steam, in the tab Steam made for them. The
  game you're playing puts its soundtrack first, and soundtracks that aren't installed install from there.
- **Games:** pick an album or playlist from any source for a game and it plays when the game starts
  (the latest link wins if several sources have one); or have the music
  pause while you play and pick up again when the game closes.
- **Several accounts:** add a service more than once (two Spotify accounts, a second Navidrome user) and
  browse them together in your library.
- **Native Quick Access tab:** Now playing, Library, Search and Settings, with a mini player while you
  browse, built from Steam's own components and controller conventions. Now playing is two pages: the
  player, then below it the song's lyrics (when there are none, the cover as a turning record with the music round it) or the queue. Settings
  has what the media center's has: the equalizer, playback, scrobbling, games and the media center's own
  choices (sources are added, edited and removed in Decky → MusicDeck).
- **Your music library:** local files as a library, not a folder list: albums (compilations kept whole, discs
  in CD1/CD2 folders together), artists with the songs they appear on elsewhere, genres, years by decade and
  composers, all from the files' tags. MusicDeck counts plays on the Deck for Recently played, Most played and
  Not played yet; star ratings in the files make Top rated. Now Playing's Info shows the file's format
  (FLAC 24-bit · 96 kHz, MP3 · 320 kbps), year, genre, composer, label, rating and plays. MP3, FLAC, Ogg,
  Opus, AAC/ALAC (M4A), WAV and WebM play as they are; AIFF, Monkey's Audio, WavPack, DSD (DSF/DFF), TAK,
  TTA, CAF, MKA, WMA, Musepack and AC-3 play through ffmpeg when it's installed (lossless ones as FLAC).
- **Ratings, stats and smarter shuffle:** rate songs 1-5 stars from the track list, Now Playing or a song's menu
  (written into the files, where other players read them); skips are counted, and smart playlists pick by rating
  and skips. **Your stats** sums up your listening from every source: listens, hours, top artists, albums and songs,
  when in the day and week you listen, days in a row. Shuffle by **songs**, **albums** (whole albums in random
  order) or **smart** (better-rated songs sooner, never the same artist twice running); save the queue as a playlist.
- **Library tools (desktop):** a clean-up report with a fix beside each problem (duplicates, albums split in two,
  albums without covers, songs missing details, files gone), **Tag tools** (tags from file names, capitalisation,
  renumbering, find and replace), **Organize files** into folders by their tags, **loudness** measured for songs
  without ReplayGain, **import** from iTunes or MusicBee (play counts, ratings, loves, playlists) and **export**
  of playlists as .m3u8: each shown before it's done, and each undoable. A **column browser** (genre, artist,
  album) and **search by field** (`artist:nirvana year:>1990 rating:>=4`). **CUE sheets** (a whole album in one
  file) show as their songs; songs **convert** to MP3, AAC, Opus or FLAC for other devices.
- **Downloads anywhere:** every playlist or album of a source that can download has Download, wherever it's
  opened (Quick Access, the media center, desktop Steam, its menu); one from another source is saved to your
  playlists first. A playlist you've saved says so wherever it's opened, and **keeps up with the original**:
  songs added to it on SoundCloud, YouTube or any source show up in your copy (and are downloaded, if it is),
  songs taken out go, and what you added or removed yourself stays that way, on all your devices.
- **Library manager for your files:** edit a track's or a whole album's tags (**Y** → Edit tags; the last
  edit to each file can be undone), fill them in with **Look up details**: [MusicBrainz](https://musicbrainz.org),
  Apple Music's and Deezer's public catalogues are all asked at once, the same record from several of them
  merged into one, and each weighed against your files (their lengths above all, then titles, the number of
  tracks, artist and year) into how sure a match it is. You pick one after seeing its cover, how sure it is,
  exactly what would change and which file becomes which track; and give albums and artists pictures: one of your own, an
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
  to edge, and every page scrolls a whole row at a time. Browse's shelves scroll sideways by controller or touch.
  Settings has the equalizer; what it opens on, background, cover
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
- **Song notifications:** a toast with the artwork when a new song starts, while you're in a game (or always, or
  never: Settings → Song notifications).
- **Cast to speakers and TVs:** Chromecast, Google/Nest speakers and anything with Google Cast, UPnP/DLNA
  devices (Sonos, AV receivers, smart TVs, an Xbox's Media Player) and Rokus. The speaker fetches the music itself; the queue, play reports and
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
- **Plug in a music player, phone or CD:** an MP3 player, an iPod in disk mode, a USB stick, a phone set
  to "File transfer", or a CD in a disc drive shows up in Local files, in its Devices tab (and first on
  Home), as on the Xbox: open it and play what's on it alongside your library. Y on an album, artist,
  playlist or song copies it to a plugged-in player, or from one to the Deck (files as they are, into
  Artist/Album). A CD is named by MusicBrainz, and its album page rips it to your Local files folder as
  tagged FLAC (with ffmpeg, which Bazzite has). No Local files source yet? Plugging something in adds one on
  ~/Music. Players whose firmware calls them by a code name can be renamed in Decky → MusicDeck, under
  Plugged in. Phones need GVfs in game mode (Bazzite has it); elsewhere, USB storage only. On Windows all of
  it works too: drives by their letters, phones through Windows' own MTP support, and an iPhone's synced
  music through Apple Devices or iTunes (installed, and trusted once).
- **Burn a CD:** with a CD drive plugged in and a blank CD-R or CD-RW in it, Y on an album, artist or
  playlist in Local files → Burn to CD writes it as an audio CD any CD player plays, gapless, with its titles
  (CD-Text) for car stereos and players that show them. It says first how long it is and whether a CD-RW is
  erased. Linux and Windows; cdrskin is bundled for each (see [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)).
- **In your Library:** Decky → MusicDeck → Interface → Show in Steam Library puts MusicDeck in the Steam library;
  Play there opens the media center.
- **Delete, safely:** Y on a song, album, folder or playlist file in Local files deletes it to the trash;
  Local files' Recently deleted puts it back. Devices can be ejected from their card or from Decky → MusicDeck,
  and say how much room they have left. A data CD of MP3s plays too, and an iPhone's synced music (read only).
- **iPods:** an iPod with Apple's software shows in Devices by its own name and model, and music copied to
  it goes into its library as iTunes put it there, so the iPod's menus play it (FLAC converted to Apple
  Lossless; playlists, and plays made on the iPod, kept), with its album's cover on the iPod's screen and its
  ReplayGain as Sound Check. Renaming it renames the iPod itself. Every iPod with a disk mode syncs, the nano
  5th-7th generation included (a nano 5G once it's been synced with iTunes); see [docs/IPOD.md](docs/IPOD.md).
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
| SoundCloud | None | Search, curated selections, what's popular now, artists (their reposts, likes, playlists, station and similar artists), playlists and albums, related-track radio |
| YouTube Music | None | Home, search across YouTube Music **and** regular YouTube, artists (every album and single, their radio, related artists, about them) and channels, albums with their radio and what's like them, radio, lyrics |
| Bandcamp | None | Genre charts, Bandcamp Radio's shows (each with what's playing in it), search, albums (tags, "you may also like", credits) and artist discographies with their bio, full-track streaming, lyrics, same-artist radio |
| Internet radio | None | Tens of thousands of stations from [Radio Browser](https://www.radio-browser.info): popular near you, trending, genres, search, favourite stations, song on air |
| Podcasts | None | Apple's top charts, search for shows and episodes (or paste a feed address), subscriptions, new episodes, episodes resume where you stopped, mark as played |
| Live Music Archive | None | Over 250,000 concert recordings bands allow to be shared ([archive.org](https://archive.org/details/etree)): concerts played on this day, popular this week, bands and their shows, search, same-band radio |
| Qobuz | Your subscription | Your playlists and favourites (synced), My Weekly Q, Qobuz's Discover (new releases, album of the week, Qobuzissimes, press awards, ideal discography), playlists by theme, radio from a song, an album or an artist, full artist pages (every kind of release, appears on, similar artists, biography), albums with what's like them and their label, search with Load more, MP3 up to 24-bit/192 kHz FLAC, plays reported as from the official player |
| TIDAL | Your subscription | Your playlists, mixes and favourites, TIDAL's home page, search, track and artist radio, artists' singles, appearances, similar artists and biography, synced lyrics, up to CD-quality FLAC |
| Soulseek | A Soulseek name (registered when you first sign in with it) | Search what other people share, albums by folder (the whole folder) with format and quality, browse a user's whole library, play a track (downloaded first), download an album into your music folder (default `~/Music/Soulseek`), where your local files pick it up and get it ready; downloads carry on after interruptions, retry and can be cancelled; optionally share folders of your own (searches, browsing, uploads two at a time), with your router asked to forward the port |
| Spotify | Premium | Your playlists, Liked Songs (synced), recently played, top tracks and artists, saved albums, new releases, search, Spotify's synced lyrics, up to 320 kbps, Spotify's own loudness levelling |

**No accounts where a service doesn't need one.** SoundCloud, YouTube, Bandcamp, radio, podcasts and the Live Music Archive are used signed
out, as NewPipe does: nothing ties your listening to an identity, and there's no account to get flagged for
using an unofficial client. Your playlists and favourites for those stay on the Deck.

**Passwords are never stored**, except Soulseek's: its protocol signs in with the password every time and
has no tokens, so it's kept like the tokens below (and never in backups). Navidrome passwords become Subsonic tokens and Jellyfin, Emby and
Audiobookshelf passwords become access tokens on save (or skip the password with Jellyfin's Quick Connect).
Plex signs in with a code entered at plex.tv/link. Qobuz, TIDAL and Spotify sign in on their own websites, in
Steam's browser, and MusicDeck only receives the resulting tokens. **Sign in with your phone** (Spotify, Qobuz,
TIDAL, Plex) shows a QR code instead: scan it, sign in or approve on your phone, done. Spotify's and TIDAL's are
their own TV-style codes (spotify.com/pair, link.tidal.com); Qobuz's phone comes back to the Deck over your home
network, so it has to be on the same Wi-Fi.

**Spotify accounts that won't play** ("audio key error", seen with some newer accounts): make a Spotify Soloist API
key at developer.spotify.com/dashboard/soloist and paste it in the Spotify source's settings. MusicDeck fetches
Soloist (Spotify's own player) from Spotify and switches the source over to it. Then, in Spotify on your phone, on
the same Wi-Fi, choose **MusicDeck (your device's name)** under Devices once. Until you do, playing stops on the
song and tells you so; once paired it plays on by itself. Your library, search and lyrics work as before; songs
played through Soloist skip the equalizer, volume levelling and crossfade, and can't be cast. The key is kept like
the tokens below.

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
| Desktop Steam → **Music** (beside Library) | The music library and player of desktop Steam, its menu on hover; the mini player when you leave it |

Controller: **A** plays or opens, **X** adds a track to the queue (removes it, in the queue) or plays an album or
playlist without opening it, **Y** opens a track's or playlist's options (including the sleep timer for the
playing track), **B** goes back, **L1**/**R1** switch tabs. Re-selecting Library or Search returns to its start.

Desktop: **Space** plays and pauses, **Ctrl+←/→** previous and next, **Ctrl+↑/↓** volume, **Ctrl+F** search,
**Ctrl+L** lyrics, **Ctrl+Q** queue (in the big Now Playing too; **Esc** leaves it), **Alt+←/→** (or Steam's arrows, or the mouse's side buttons) back and
forward, **Ctrl+A** picks every song in a list, **Enter** plays the picked ones. Double-click a song to play from
it; right-click anything for its menu.

On Now playing, down from the player is its second page: the lyrics, or the queue (switched at the page's
top). The queue lists tracks in the order they'll play (shuffled or not); **Clear** at its top empties it
(after asking); **Y** on a queued track moves it (play next, up, down, to the end), and **Move** picks it up so
the d-pad carries it until **A** or **B** puts it down.

Switching the live source (in the Quick Access panel) stops playback and clears the queue; nothing from the
previous source stays queued.

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
HLS streams (some radio stations, SoundCloud, TIDAL). Renderers get the title, artist, album and cover
with each song (DIDL-Lite with DLNA profiles); an Xbox's Media Player plays but doesn't show them yet.
Rokus play through Roku's own player, with the title, artist and cover on screen, but can't jump within a
song or have their volume set from MusicDeck; a Roku has to allow it first (on the Roku: Settings > System >
Advanced system settings > Control by mobile apps > Network access: Default or Permissive). Devices are
looked for on every network the computer is on (a PC's WSL, Hyper-V or VPN adapters hid them before). On
desktop Steam, the cast button is in the player along the bottom.

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
plugin from ZIP** with the zip for your system (its SHA-256 is in the `.sha256` file beside it). After that it
updates itself: when a new version is out, a notification says so, and **Settings → Update to x.y.z** hands it to
Decky's installer, which asks first and keeps your settings, playlists and downloads:

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
zip (python.org's embeddable Python; nothing to install); a phone's songs are copied to the PC as they're
needed (up to 2 GB of them kept), since Windows doesn't show a phone as folders the way Linux does; a
Mac-formatted iPod can't be read (Windows can't read HFS+: MusicDeck says so); an iPhone needs Apple Devices
(or iTunes) installed, as Windows has no iPhone driver without it; screen off and lowering the music in
games depend on Steam's game mode, which Windows doesn't have; the Steam soundtracks source finds Steam
where Windows' registry says it is. Decky's team doesn't support Windows: ask
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

Not planned yet:

- **Data and MP3 CDs** (files rather than CD audio): would need xorriso besides cdrskin.

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
requests (Apache-2.0), QuickJS-NG (MIT), hls.js (Apache-2.0), librespot (MIT, in spotify-helper), hashab
(dstaley/hashab, public domain: The Unlicense, in backend/hashab), cdrskin (libburn 1.5.8, GPL-2.0+, built
unchanged from its release: backend/cdrskin/UPSTREAM). The iPod code follows libgpod
(LGPL-2.1-or-later): ipod_schema.py is generated from its itdb_sqlite_queries.h, ipod_sqlite.py is ported
from its itdb_sqlite.c, and ipod_artwork.py from its db-artwork-writer.c and ithumb-writer.c.
