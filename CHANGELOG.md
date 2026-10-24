# Changelog

What changed in each version, newest first. Every build put on a device has its own number
(see [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#versions-and-releasing)).

## 0.18.0

- **Likes in each source's words:** Y on a song says Save to Liked Songs on Spotify, Add to My Collection on
  TIDAL, Add to your stations on radio, Like on SoundCloud, YouTube Music and Bandcamp, and Add to favourites
  elsewhere; the note after it says the same.
- **SoundCloud, YouTube Music, Bandcamp and the Live Music Archive can like songs**, kept on the Deck (they're
  used signed out): a heart wherever the song shows, and a Liked songs list in Browse and the panel's Library.
- **The visualizer no longer gets stuck:** when the page changed size while the music was paused, its resting
  dots were stretched into tall smudges until the music played again. It redraws at the new size.
- A song without lyrics shows just the visualizer, without "No lyrics for this track". After MusicDeck
  reloaded with the media center open, that space could stay empty: it's measured again once the song is back.

## 0.17.0

- **Keep albums, artists and playlists in your library on the service**, from their pages in the Quick
  Access panel and the media center:
  - **Spotify:** Add to library for albums and other people's playlists; Follow for artists.
  - **Qobuz** and **TIDAL:** the same (they keep them as favourites and in My Collection).
  - **Navidrome**, **Jellyfin**, **Emby** and **Plex:** Add to favourites for albums and artists (starred on
    Navidrome, five stars on Plex).
  - The button shows whether it's there already, and what you add shows in the source's Albums, Artists,
    Playlists and Favourites at once.
- Plex's Favourites list your favourite artists too.
- Spotify asks for permission to follow artists when you sign in. Sources signed in before this version
  say so when you first follow someone: edit the source and sign in again.

## 0.16.0

- **One grid, the same everywhere** (Show all, genres, moods, countries, stations, albums):
  - **Width sets the columns.** Covers are about as big as on the rows elsewhere (the Cover size choice)
    and fill the width edge to edge, names under them again.
  - **Height only caps the size.** Covers never get so big that a whole row doesn't fit under the title.
  - **Only whole rows.** As many whole rows show as fit, centred, sliding in a row at a time.
  - **No reshaping.** The grid's shape no longer changes when the mini player appears or goes, and it
    doesn't jump between shapes.
- The covers-only look and the name beside the title are gone.

## 0.15.1

- **Grids stop flickering:** a grid flipped between two shapes (three big covers, then four with a row
  cut off) over and over. It measured the room under its title from where its covers began, which moves
  with the shape it picks; it measures from the title's foot now, and settles on one shape.

## 0.15.0

- **Grids fill the screen on their own:** the columns-and-rows setting and the Layout button are gone. A
  grid picks its shape for the screen it's on: as many rows as keep the covers a good size, and covers
  sized to fill the width edge to edge and the height under the title (2 rows of 6 on a small screen,
  3 rows of 6 on a Steam Deck). The cover in focus is named beside the grid's title.

## 0.14.2

- A chosen grid layout stays one block in the middle, spaced the same across as down, rather than spread
  to the screen's edges.

## 0.14.1

- **Grid layouts show the rows you chose:** with names under every cover, a short screen couldn't fit many
  rows (5 × 5 showed one row of tiny covers). Layouts too dense for names now show the covers alone,
  closer together, filling the rows asked for, and the one in focus is named beside the grid's title.
  Roomier layouts keep the names under the covers.

## 0.14.0

- **Choose a grid's layout:** columns 1 to 5 and rows 1 to 5 in any combination (1×1, 2×3, 5×5...), or as
  many as fit. The covers are sized to fill exactly that many on screen, so nothing is cut off. Set it from
  any grid's **Layout** button at its title (it shows the layout, e.g. "4 × 2"), or in Settings → Media
  center. It applies to every grid: Show all, genres, countries, stations, albums.

## 0.13.6

- **Show all grids fit the screen:** the grid worked out the room under its title wrongly and showed one
  row more than fits, the last cut off at the bottom even when focused. It shows only the rows that fit,
  whole, and slides the rest in.

## 0.13.5

- **Genre pages fixed:** a genre's grid opened pushed down the screen, its albums cut off at the bottom
  under an empty space. The grid fits itself to the screen again, rows from its top.
- Rows on a page keep to one screen each however they're spaced (the space between rows no longer
  counted toward a row's height, which could make a row take two screens).

## 0.13.4

- **Search keyboard, fixed for real:** Steam's keyboard shrinks the page rather than the window, which the
  last fix didn't see. The media center now measures itself: with the keyboard up, the mini player steps
  aside and the search field and results sit right above the keyboard.
- **Rows centred, none peeking:** each row sits in the middle of the screen, and the next row no longer
  shows its first few pixels at the bottom edge.
- **Squares, not rectangles:** Your music is a row of square cards like albums, each a 2×2 of four of its
  covers (one cover when there are fewer), with its name and count below. Genre, mood, country and language
  grids are squares too, as big as album covers.

## 0.13.3

- **Long rows end in Show all:** any row longer than 16 items (an artist's albums, search results, Home's
  new releases, recently played songs, stations near you) shows its first 15 and a square Show all. Show
  all opens the full list in the standard grid (songs as a list).

## 0.13.2

- A genre's (or mood's, decade's, chart's) Show all opens the same grid as every other Show all, whole
  rows filling the screen, instead of a sideways row of its albums.

## 0.13.1

- **Genres as rows:** Browse shows a row per genre (Pop/Rock's albums, then Jazz's, then Classical's...),
  each loading as you reach it and ending in a square **Show all** that opens all of that genre. Moods,
  countries and languages work the same way. Every row ends in the same Show all square.
- Qobuz genres go well past their row: Show all lists the genre's most streamed albums three pages deep,
  then what's new.
- **Go to album / Go to artist** from a track's menu opens the page in the media center (it opened in the
  Quick Access panel, out of sight).
- **Search keyboard:** opening it no longer pushes the mini player and Steam's bottom bar over the search
  field; the mini player steps aside while the keyboard is up.
- **Holding the stick along a row** glides smoothly instead of stuttering, then catching up.
- **Rows always whole:** a browsing page's rows share one height, so the screen holds whole rows (one at a
  time where only one fits) and never half of the next; the last row comes up under the tabs like the
  rest. Nothing is faded or hidden any more.

## 0.13.0

- **Grids show whole rows:** a grid fills the screen with as many whole rows as fit, centred, and slides
  the next one in as you move down. No half rows, and no fading.
- Browse's sections past your library are rows of their first items ending in Show all, which opens the
  whole grid.
- Holding the stick up or down glides steadily instead of stuttering.

## 0.12.5

- Large Browse tiles without covers keep their icon in the top corner, clear of the name.

## 0.12.4

- Tiles without covers no longer show their icon over the name.

## 0.12.3

**Browse filled out for every source**
- Every tile says what's in it ("19 playlists", "60 albums", "35 genres") and shows covers from it. These
  load in the background a moment after Browse opens, and opening the tile right after is instant. A tile
  with nothing in it yet says "None yet".
- Your library in its own tiles instead of one long Library page:
  - **Navidrome**, **Jellyfin/Emby** and **Plex:** Albums, Artists, Playlists, Favourites (Starred on
    Navidrome).
  - **Qobuz:** Playlists, Albums, Artists, Songs.
  - **TIDAL:** Playlists, Your mixes, Albums, Artists, Songs.
  - **Spotify:** Liked Songs, Playlists, Albums, Artists.
- **For you:** a server's Recently added, Recently played, Most played, Highest rated (Navidrome) and
  Random albums; Spotify's Your top.
- **Qobuz's Explore:** Playlists by Qobuz, Press awards, Best sellers, Ideal discography and Most featured,
  each a tile of its own.
- **Internet radio:** Your stations. **Steam soundtracks:** Soundtracks and Favourites.
- Playlists made in MusicDeck get a "Your playlists" tile on sources without a Playlists tile, and join the
  source's own Playlists where it has one (local files included).
- **Layout:**
  - Large tiles fit four to a row, each with two covers side by side.
  - Names and lines stay on one line.
  - No two tiles lead with the same cover.
  - Text over covers is easier to read.

## 0.11.3

- At the end of a page, a row left half under the tabs is hidden too, so the top edge is as clean as the
  bottom.

## 0.11.2

- A heading at the bottom of a page (Browse's "On this Deck") no longer shows cut off without its tiles: it
  shows with them as the page moves down.

## 0.11.1

- Top genres each show a different cover where the library has one, and leave out genres of a single song.

## 0.11.0

**A fuller Browse for local files**
- Your music: Albums and Artists, plus **Songs** (every track), **Playlists** and **Favourites**, each with
  its covers.
- **From your listening:** Recently played, Most played, Not played yet and Top rated, each with a cover.
- **Top genres:** your biggest genres as tiles of their own, a press from their songs.
- Folders say how much space the library takes. Genres, Years and Composers only show when the files are
  tagged with them.
- Long track lists (every song in a big library) open at once and fill in as you scroll.

## 0.10.3

- The media center's pages end on whole rows: a row only partly in view at the bottom (a grid's covers,
  a shelf, a track) stays hidden until the page moves to it, so nothing shows cut in half.
- Every page scrolls the same way: track lists, search results, recent searches, and the queue and
  related lists beside Now Playing glide by whole lines like the grids and shelves, instead of Steam's
  jumpier scrolling.

## 0.10.2

- Grids in the media center move by whole rows: the top of the page always starts on a row, never half a
  row under the tabs; going back to the first row shows its heading again.

## 0.10.1

- Radio genres named with spaces showed "%20" in their titles ("Hip%20Hop"); they read as they should.
- Moving through grids in the media center (Browse's tiles, all albums or artists, genres, stations) glides
  smoothly, as shelves do, scrolling only as far as shows the whole item, instead of Steam's juddering
  scroll that could leave a row cut off.

## 0.10.0

**Soulseek, much closer to a full client**
- Albums open with the user's whole folder, not just the files that matched; **Everything <user> shares**
  browses their library.
- Downloads carry on from where they stopped, try again up to three times, can be cancelled, and show how
  far along they are.
- **Share folders** (in the source's dialog; nothing is shared until you pick one): other users can
  search, browse and download your music, two at a time, and the server is told what you share.
  Your Deck takes part in the network that passes searches along, to hear them.
- Your router is asked to forward Soulseek's port (UPnP), so more users can reach you.
- Answer to "does sharing from another device count?": no. An account is signed in at one place at a
  time, and signing in on the Deck signs the other device out; share from the Deck instead.

**Internet radio**
- On air leaves out the stream quality, and shows two languages at most, so it never scrolls.

## 0.9.1

**Internet radio**
- Browsing (a genre, a country, a search) shows a grid of stations, not a playlist: A plays that station
  on its own, Y gives its options. Pages of stations have no Play all or Shuffle.
- **Your own lists of stations**: Y on a station → Add to playlist (or + while it plays). A list plays from
  the station you pick, and previous and next move through it.
- Radio controls: favourite, previous station, play or stop (live radio doesn't pause), next station, and
  add to a list, in place of shuffle and repeat. Previous and next are greyed out for a station played
  on its own.
- Every station's picture fills its square: a square one edge to edge, a wide or tall logo whole on its
  own background colour (no blur).
- On air no longer has the LIVE / Listening for line above everything.

## 0.9.0

**Soulseek** (new source, under File sharing)
- Search what other people share: albums by folder, with format and quality, and the best places to get
  them first (a free slot, speed); tracks too.
- Play a track: it's downloaded first, usually in seconds; a busy user's queue is said, and the download
  carries on.
- **Download album** saves it into your music folder (default `~/Music/Soulseek`), where your local files
  notice it and get it ready like any new album. Home shows what's downloading and what's done.
- Sign in with a Soulseek name and password (a new name is registered for you). Soulseek has no sign-in
  tokens, so the password is kept (never in backups). Nothing is shared from your Deck.

**Internet radio**
- On air's station details are plain text: language, quality, today's listeners (its place and genres
  are already under its name).

## 0.8.4

- On air reads stations that pad the song with themselves ("Theme from Joy House by Jimmy Smith - Adroit
  Jazz Underground on walmradio.com"): the song and artist show, the station's part doesn't. A station's
  slogan with its name ("Radio Bob - Rockt!") isn't taken for a song. Web addresses in titles are caught
  again (0.8.3's check for them never matched).

## 0.8.3

**Internet radio**
- **Jump back in** shows the stations you listened to, latest first, instead of the genre or country page
  you found one on.
- **On air** no longer shows what isn't a song: stations that send their own name, their playout system's
  codes ("PLAY_8_NEW"), adverts, idents or web addresses where the song should be are shown as live, with
  nothing made up.
- On air fits its space and doesn't scroll: the station's facts stay on one row, and only as many earlier
  songs show as fit. The Station website button is gone.

## 0.8.2

- On air doesn't repeat what the line under the station's name says (its place, its first genres).

## 0.8.1

- Radio's **On air** keeps to what's only there: the song when the station says (and what it played
  earlier), how long you've listened, the station's facts, and a button to its website. No visualizer,
  and no row of stations like it (that's Similar).

## 0.8.0

**Quick Access**
- The Equalizer tab is now **Settings**, with everything the media center's Settings has: the equalizer,
  playback, scrobbling, games, and the media center's own choices. Values sit under their labels, so none
  is cut short. Sources are still picked in Decky → MusicDeck.

**Internet radio**
- A station's logo fills its square, whole, over a soft blur of itself; a station without one gets its
  initials on a colour of its own. When a station names the song, its cover takes the square, looked up
  in Apple's catalogue when the station doesn't send one.
- **On air** is a page of its own now: the song and artist when the station says; how long you've been
  listening; where the station is from, its language, quality, today's listeners and genres; what it
  played earlier. The Station tab is folded into it (On air · Similar).

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
