# Changelog

What changed in each version, newest first. Every build put on a device has its own number
(see [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#versions-and-releasing)).

## 0.8.0

**Quick Access**
- The Equalizer tab is now **Settings**, with everything the media center's Settings has: the equalizer,
  playback, scrobbling, games, and the media center's own choices. Values sit under their labels, so none
  is cut short. Sources are still picked in Decky → MusicDeck.

**Internet radio**
- A station's logo fills its square, whole, over a soft blur of itself; a station without one gets its
  initials on a colour of its own. When a station names the song, its cover takes the square, looked up
  in Apple's catalogue when the station doesn't send one.
- **On air** is a page of its own now: the song and artist when the station says, or the sound itself,
  drawn, when it doesn't; how long you've been listening; where the station is from, its language,
  quality, today's listeners and genres; what it played earlier; and stations like it, one press away.
  The Station tab is folded into it (On air · Similar).

**Local files: more places to look**
- **Look up** searches Apple Music's and Deezer's public catalogues beside MusicBrainz (no account), so
  albums MusicBrainz doesn't know (new releases, small labels) can be filled in too; each result says
  where it's from.
- Covers come from the Cover Art Archive, then Apple's (up to 1200 px) or Deezer's.
- **Jump back in** shows albums and artists as they are now: new pictures, and albums renamed by a tag fix
  under their new names.

## 0.7.0

**New music gets ready by itself (local files)**
- Albums copied into your music folders are noticed as they arrive (also hourly, and at start-up), and
  each new one gets ready as you've set the source (its Edit dialog, "When new music arrives"):
  - **Covers and artist pictures:** on.
  - **Tags from MusicBrainz:** **Ask me** (default): new albums MusicBrainz knows wait under Browse →
    **To review**; Y → Look up on MusicBrainz shows what would change, and nothing is written until you
    apply. **Only when sure**: a match with no doubt fills in only fields your files leave empty. Or off.
  - **Lyrics:** off unless you turn it on.
- A note says what each batch got ("2 new albums ready: 2 covers, 1 to review").
- A library's first scan isn't treated as new music.

**Lyrics kept your way**
- Lyrics are kept **in MusicDeck** by default, so your music folders stay exactly as they are. Or choose
  **beside the songs**: an `.lrc` named like each song, the format Navidrome, Jellyfin, Plex, foobar2000
  and Poweramp read, with the song's names and length at the top.

## 0.6.2

- Browse's "Your music" tiles are wider than tall, so Explore shows below them without scrolling as far;
  Folders says how many there are.

## 0.6.1

- Lyrics for FLAC and Ogg files: looking in their tags for lyrics failed, which stopped the online lookup
  as well, so tagged FLAC and Ogg files never showed lyrics unless an .lrc sat beside them.

## 0.6.0

**Browse, organized**
- Grouped: **Your music** (albums, artists, composers, your library) as large tiles showing your own
  covers or artists' pictures and how many there are; then **Explore** (genres, years, charts, moods,
  countries...) and **On this Deck** (folders) as compact tiles with an icon and a count.

**Local files**
- **Artists' pictures:** find one online (Deezer's public catalogue, for the artist of exactly that name;
  no account), set your own, or remove it. They show on the Artists page, the artist's page and Browse.
  "Find missing artwork" now finds artists' pictures as well as album covers.
- **Lyrics kept with your music:** "Save lyrics to file" on a track, "Find lyrics" on an album, and
  "Find missing lyrics" for the whole library (Decky → MusicDeck → the source's menu). What the lyrics
  sites have is saved beside each track as an `.lrc` (synced when they are), so it works offline and in
  other players. Tracks that have lyrics already are left alone.

## 0.5.2

- The media center has the same five tabs for every source: **Now Playing, Home, Browse, Search,
  Settings**. A source's other sections (Albums, Artists, Genres, Years, Folders; Charts, Moods;
  Countries, Languages...) are tiles in **Browse**, each opening as a page; B comes back to its tile. A
  source with one such section shows it in Browse directly.

## 0.5.1

- Covers found online were saved cut short (the top part of the picture only): the whole picture is
  saved now. Find the artwork again for any album that got a half cover.
- The tag editor, the MusicBrainz list and its preview open at their top with the controller on the
  first field or entry, instead of on the buttons at the bottom.
- The tag editor puts Track, Disc and Year on one row, so it scrolls less.
- A MusicBrainz preview says a change shared by several tracks once ("Every track (12): Artist
  radiohead → Radiohead"), and each entry is a stop for the d-pad, so a long preview scrolls.
- The MusicBrainz list has only Cancel at its foot (picking a release is the action).

## 0.5.0

**Local files: a library manager**
- **Edit tags** of a track, or of a whole album at once (fields its tracks differ on show as Mixed and stay
  as each file has them unless you change them). The last edit to each file can be undone.
- **Look up on MusicBrainz:** pick the release your album is, see exactly what would change (album
  artist, year, genres, label, track titles and numbers), then apply. Track by track only when the
  release has as many tracks as your album.
- **Artwork:** set a picture of your own for an album, find its cover on the Cover Art Archive, or find
  every missing cover at once (Decky → MusicDeck → the source's menu → Find missing artwork). Kept in
  MusicDeck, so your music folders aren't touched, and "Use the files' artwork" goes back.
- Files are never written in place: each edit goes to a copy that replaces the file in one step.
- Hidden files (macOS's `._` leftovers) are no longer counted as music.

**Playlists**
- Navidrome, Jellyfin, Emby, Plex, Qobuz, Spotify and TIDAL can make a **new playlist** on the service from
  any track (Add to playlist → New playlist), not only add to ones already there.

## 0.4.2

- Moving between Now playing and the other tabs in the Quick Access panel (L1/R1, or A on a tab) no longer
  loses the controller's place: the next press used to go to finding focus again, which could land back
  on Now playing.

## 0.4.1

- Now playing's cover in the Quick Access panel shrinks only as much as the player needs: the empty space
  under the volume slider no longer counts, so the cover stays bigger on a short panel.

## 0.4.0

**Local files as a music library**
- Albums, artists, genres, years (by decade), composers and folders, each a tab in the media center
  (Composers only when your files name one).
- Compilations without an album-artist tag stay one album ("Various artists"), and discs in `CD1`/`CD2`
  folders stay together. An artist's page adds the songs they appear on elsewhere.
- Several genres per file ("Rock; Pop"), the original year over a reissue's, composer, label and star
  ratings are read from the tags. Your library is scanned again once to read them.
- Plays are counted on the Deck (with Report plays on) for new Home rows: Recently played, Most played and
  Not played yet. Files rated four stars or more make Top rated.
- Now Playing in the media center has an **Info** panel for local files: format and quality
  (FLAC 24-bit · 96 kHz, MP3 · 320 kbps), year, genre, composer, label, rating, plays and the file.
- An album's page says what it is: artist, year, tracks, length and format.
- Search also finds genres and composers.

**Player**
- The Quick Access visualizer is an oscilloscope, which suits the panel's square space better than bars
  (the media center keeps its bars).
- When a casting line or a long title makes Now playing's first page too tall, the cover shrinks instead
  of the volume slider being pushed onto the second page.
- On a squarer screen (docked), the media center's cover no longer takes most of the width.

**Media center**
- Settings has Playback, Scrobbling (ListenBrainz, Last.fm) and Games, as in Decky → MusicDeck.
- Charts, genres and other tabs that are the same for everyone are kept for ten minutes, so they don't
  load again after you favourite a track or edit a playlist.
- Covers load as they scroll into view instead of all at once.

**Fixes**
- A source restarted twice at once (a sign-in renewing while you save it) could leave a copy of it running.
- A source removed while it signed in came back.
- A song un-marked for download while it downloaded was kept anyway, and songs marked as the downloader
  finished waited for the next change.
- Pages opened while an edit was being saved could show the list from before the edit when you came back.
- The YouTube helper could start after its source was removed.
- Play reports sent in the background could be dropped before they finished, with no word of why.
- The casting listener is let go when the plugin unloads (one was left behind per reload).

**For developers**
- `pnpm smoke` tests the built bundle: it loads, wires its player and casting, and unloads leaving nothing
  running.
- `scripts/package.py` names the zip by version (`MusicDeck-0.4.0.zip`), writes its SHA-256 beside it,
  includes this changelog, and refuses a build older than the sources.

## 0.3.9

- ListenBrainz's **Get your token** and Last.fm's **Create an API account** open their page over the
  dialog instead of behind it. The dialog comes back as it was when you close the browser, with a token,
  key or secret you copied there pasted in.
- No ellipses on buttons and menu items (Link, Restore, Edit, Rename...). They read as text cut short.

## 0.3.8

- **Add a source** groups the kinds of source in submenus (your files and servers, streaming services,
  radio and podcasts), each A to Z.
- Dropdowns with long values (the live source, In games, the EQ preset) sit under their label at full
  width instead of being cut short.
- Now playing with nothing to play shows a quiet note, with Library and Search to go find something.

## 0.3.7

- A tidier Decky settings page: sources and the kinds to add are A to Z, with Playing from at the top.
- Report plays, ListenBrainz and Last.fm have a Scrobbling section of their own.
- The debug log shows its file's name, not a path over two lines.
- The version shown is always the one running.

## 0.3.6

- Casting works again: the player had stopped hearing from the device (stuck loading, and play/pause
  loaded the track on it again).
- While casting, the speaker's name replaces the album on the card, so the player keeps its size.

## 0.3.5

- Fixed a code comment showing as text over the queue.
- Synced lyrics start right under the header, without fading the first line out.

## 0.3.4

- A hairline under the Lyrics / Queue header, as under the tabs, and no gap above the queue.
- Synced lyrics start at the top until the song reaches them.

## 0.3.3

- Now playing's second page has a quiet header (Lyrics, Queue) instead of pills, with **Clear** at the
  top of the queue, so a long queue is cleared without scrolling to its end.
- The player fits its page with the tabs (a smaller cover).

## 0.3.2

- The tabs are part of Now playing's first page and slide away with it, so the lyrics or the queue get
  the whole panel.

## 0.3.1

- Now playing's two pages slide a whole page in a fixed frame, instead of stopping half way.

## 0.3.0

- Now playing and the queue share one tab, as two pages: the player, then below it the lyrics or the
  queue. The Queue tab is gone (Now playing, Library, Search, Equalizer).

## 0.2.0

The first numbered release. Highlights of what came before it:

- Sixteen sources, one live at a time: local files, Steam soundtracks, Navidrome/Subsonic, Jellyfin,
  Emby, Plex, Audiobookshelf, SoundCloud, YouTube Music, Bandcamp, internet radio, podcasts, the Live
  Music Archive, Qobuz, TIDAL and Spotify.
- The media center: the player full screen in Steam's main window, with tabs made for each source, and
  Now Playing made for what's playing (lyrics and queue; on air; show notes; chapters; setlists).
- Casting to Google Cast and UPnP/DLNA speakers and TVs.
- Game music, and music played quieter or paused while you play.
- An EQ for each output, ListenBrainz and Last.fm scrobbling, downloads, backup and restore.
- A visualizer when a song has no lyrics, and audio elements made only while playing.
