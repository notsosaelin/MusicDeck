# Changelog

What changed in each version, newest first. Every build put on a device has its own number
(see [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#versions-and-releasing)).

## 0.36.0

What iTunes, MusicBee, foobar2000 and Winamp have that MusicDeck didn't:

- **Rate songs**, 1 to 5 stars: in desktop Steam's track lists and big Now Playing, and from any song's menu
  (Game Mode too). Ratings go into the files (MP3, FLAC, Ogg, Opus, M4A) where other players read them; MusicDeck
  keeps the rest. **Skips** are counted (a song skipped after a moment, before 20 seconds), and smart playlists pick
  and sort by rating and skips.
- **Your stats** (desktop Steam, under Home): every song you listen to, from any source, adds up into listens,
  hours, top artists, albums and songs, the hours and days you listen, and your days in a row, for the last week,
  month, year or all time. The history starts with this version.
- **Shuffle your way**: by song, by album (whole albums in random order, as Winamp and foobar2000 do), or smart
  (better-rated songs sooner, never the same artist twice in a row). Right-click shuffle on desktop, or Settings.
  **Save the queue** as a playlist (Up next, and Game Mode's queue).
- **Library tools** (desktop Steam, Local files): a clean-up report with a fix beside each problem: duplicates
  (keep the best copy), albums split in two (give them one album artist), albums without a cover (find them),
  songs missing details, files gone (rescan).
- **Tag tools** for many songs at once: tags from file names, Title or Sentence case, renumbering, find and replace.
  **Organize files** into folders by their tags (`{albumartist}/{album}/{track} {title}`), lyrics and covers
  along. Both show exactly what they'd change first, and both can be undone. From Library tools, an album's menu,
  or several songs'.
- **Loudness for every song**: songs without ReplayGain can be measured (ffmpeg) and given it, so volume
  levelling evens them out too.
- **Move in from iTunes or MusicBee**: their library file brings play counts, skips, last plays, ratings, loved
  songs and playlists for the songs you have. **Export playlists** as .m3u8 for other players. Backups now keep
  ratings, play counts, smart playlists and your listening history too.
- **Column browser** (genre, artist, album, like iTunes's) and **search by field**: `artist:nirvana year:>1990
  rating:>=4 genre:jazz plays:0 format:flac`. "The Beatles" sorts under B.
- **CUE sheets**: an album ripped to one file with a .cue shows as its songs, and each plays on its own.
- **Convert** songs or an album to MP3, AAC, Opus or FLAC (tags and cover kept), for a phone or a stick.
- **Downloads, everywhere they belong**: Download was only on a MusicDeck playlist's page in Quick Access, opened
  from your playlists. Now every playlist and album of a source that can download has it, wherever it's opened
  (Quick Access from anywhere, the media center, desktop Steam, its "..." menu); one from another source is saved
  to your playlists and downloaded there. A playlist you've saved shows Saved (opening yours) instead of Save,
  wherever it's opened.

## 0.35.1

- **Rokus found on Linux** (the Deck) too: their answers reach Linux in pieces, and MusicDeck read only the first
  piece, so every Roku looked broken and was left out. Replies are read to the end now; the same fix covers
  speakers' and TVs' replies over UPnP, and the router's for Soulseek's port.
- **The mini player**:
  - Its window buttons over the cover are gone: right-click has them (Open Music, keep on top, close).
  - Casting from the mini player: the same speakers and TVs as the main player, listed in the mini player's
    window.
  - In the card (the tall shape), the controls span the progress bar, shuffle and repeat right under its times.
- Big Now Playing's dots have no name beside them any more (it said "Lyrics" for songs without any); each says
  what it is on hover, and in Game Mode in the footer.
- Casting's list says "This PC" on Windows rather than "This Deck".

## 0.35.0

- **The mini player, redone properly**:
  - Its window settles into one of three shapes when you let go of its edge, so the cover always shows whole:
    a square the cover fills, a card with the song below the cover, or a strip.
  - A real progress bar, inset and rounded with the times either side (a thin line across the cover at rest
    in the square), instead of a line stuck to the window's edge.
  - The controls evenly spaced (each in a slot of one width), with shuffle and repeat either side of play.
  - Volume is a button that opens its slider (with mute and the percentage); in a strip the slider takes the
    controls' place for a moment rather than covering anything. The wheel over the player still sets it.
  - The window's options (Open Music, keep on top, close) on right-click, and under "..." in the strip, where
    on hover they covered the controls.
- **Casting from Windows finds devices**: discovery now asks on every network the PC is on. Before, the
  question went out of one adapter of Windows' choosing, often WSL's, Hyper-V's or a VPN's, and nothing
  answered.
- **Rokus**: Roku players and Roku TVs show up to cast to, and play through Roku's own player with the song's
  title, artist and cover on screen. A Roku has to allow it first; one that doesn't says where to change it
  (Settings > System > Advanced system settings > Control by mobile apps). They can't jump within a song or
  have their volume set from here.
- **Albums' and playlists' "..." menu** was empty for some sources (a small blue square): it always offers
  Play next, Add to queue and Add to playlist now, in desktop Steam and in Game Mode.
- **Big Now Playing**: the last lines of the lyrics and the queue scroll clear of the fade at the bottom. Lyrics,
  Up next and Art are dots now, the chosen one a white pill with its name beside it; the same in Game Mode's
  media center (R2 still goes round them).
- Known: an Xbox's Media Player plays what's cast but still shows no title or cover. It ignores the standard
  track details MusicDeck sends (it never even fetches the cover); finding what it reads needs a test watched
  on the TV.

## 0.34.0

Desktop Steam, from a round of use of 0.33.0:

- **The player along the bottom, tidied** as Spotify's is: the song on the left (its title opens the album,
  the artist the artist), shuffle, previous, a white play button, next and repeat over a seek line in the
  middle, then lyrics, queue, casting, volume, the mini player and the big view on the right. The equalizer
  and settings moved under **...**; the visualizer to the big Now Playing. Seeking and volume are dragged
  (they only took a click), and a click on the speaker mutes. As the window narrows, the volume's slider
  and then the mini player button give way rather than squeezing the rest.
- **The big Now Playing, rearranged** after Apple Music's and Spotify's: the library steps aside and the whole
  section (the player too) lies on a blur of the cover; a **Hide** button and the Lyrics / Up next / Art switch
  along the top (their headings were cut off before); the cover sized to the room there is, never squashed.
  Lyrics without timings read in full white; synced ones light up line by line. Up next shows covers and
  lengths. The side panel has a title and a close button.
- **The mini player, redrawn** after Spotify's: it opens square with the cover filling it, the controls
  showing when the mouse is over it, and a thin progress line along its edge. Make it tall and the cover
  sits above the song; make it short and wide and it's a strip of cover, names and controls. It opens where
  and at the size you left it.
- **Dialogs resize** (Settings, the equalizer, Look up details...): Steam made them fixed in size, whatever
  MusicDeck asked. Their content fills the window as it's resized: Settings showed a black bar beside it.
- **Look up details' search** by artist and album did nothing: the button and Enter search now. Releases
  that look alike say their format and country.
- Rows of albums on Home have no scrollbar: arrows at either end on hover, and the wheel, move them along.

## 0.33.0

- **A big Now Playing in desktop Steam**: click the cover in the player (or the expand button beside it) and
  the song takes the whole Music section, its cover large over a blur of itself, with its lyrics, what's up
  next or just the art beside it. The side panel and the big view never show together: the lyrics or queue
  you had open go along into the big view and come back when you leave it (Esc, or the button again).
- **Look up details, better at finding the right album**: MusicBrainz, Apple Music and Deezer are asked at
  once, the same record from several of them merged into one (MusicBrainz's track titles, the best cover,
  the year, label and genres from whichever has them), and every record is weighed against your files,
  above all by the songs' lengths, into how sure a match it is (100% for a record whose every track is the
  right length). Nothing to choose between sources: the best is simply first.
  - On the desktop, a window of its own: the records with their covers and how sure each is, and for the
    one picked its cover large, what would change, and which file becomes which track with both lengths
    (✓ within 3 seconds). A search by other names when the tags are too far off.
  - In Game Mode, the records as a grid of covers with how sure each is.
  - New albums tagged without asking ("Only when sure") only from a record at 90% or more that's well
    ahead of the next one.
- **More formats**: AIFF, Monkey's Audio, WavPack, DSD, TAK, TTA, CAF, MKA, WMA, Musepack, AC-3 and ALAC
  in M4A now play, converted by ffmpeg the first time (lossless ones to FLAC, so nothing is lost) and kept
  for next time. WMA's tags are read.
- **Casting to Windows Media Player and TVs**: each song now goes with its title, artist, album and cover in
  the form DLNA renderers expect, and the stream answers as they want it to. An Xbox's Windows Media Player
  showed a bare ":sourcereceiver:" before. (Tried by the tests, not yet on an Xbox.)
- **Desktop fixes**, from the first round of use:
  - The **mini player** showed nowhere (it opened hidden). It's back, and opens by hand from the player's
    button too; a pin keeps it above other windows. Opened by hand, it stays until you close it.
  - The **cast button** is in the desktop player.
  - Dialogs (settings, the equalizer, smart playlists...) can be **resized**, open at a size that fits what
    they hold, and are titled "MusicDeck Settings" and so on rather than "Decky Dialog".
  - **Dragging music files** from the desktop onto Music works (it showed a "no entry" sign): "Drop to add to
    Local files" shows while you drag. "Add music files…" in the sidebar picks them instead.
  - Right-click menus open where you clicked.
  - **Library** in Steam's top bar no longer stays underlined while Music shows.
  - Coming back to Music from a page with an address bar (Store, Community, Help) showed that page over
    Music; Music now steps aside for it and comes back whole.
  - The mouse wheel scrolls the page when over a row of albums; pages use wider grids where there's room.
  - Albums and playlists without a cover show a music icon instead of nothing.
- **Steam soundtracks**: one installed while MusicDeck was running still showed as "Not installed" on Home;
  a finished download is noticed now, and a toast says it's ready to play.

## 0.32.0

- **MusicDeck in desktop Steam**: a **Music** tab between Library and Community, with a section of its own,
  built the way Steam's library is: your sources' music on the left (Home and each source's own tabs, your
  playlists, smart playlists, what's plugged in), the page on the right, and the player along the bottom.
  - Albums, artists and playlists open headed by their art, with a green Play and Shuffle; track lists sort
    by any column, take Ctrl and Shift to pick several, and play from where you double-click.
  - Right-click a song, album or playlist for its menu: Steam's own menus, with everything MusicDeck's track
    and album menus do in Game Mode (Play next, Add to playlist, Copy to a device, Burn to CD, Edit tags,
    Delete...). Several songs picked: Play, Play next, Add to queue, Add to playlist, Copy to a device, Get
    Info for all of them at once.
  - **Smart playlists**: Local files' songs picked by rules (artist, album, genre, year, plays, when they were
    added or last played, favourites, length), matching all or any, sorted and limited, as iTunes's are.
  - **Drag and drop**: songs and albums onto a playlist or a device in the sidebar; music files from your
    desktop into Local files (an Imported folder).
  - **The player**: the queue and synced lyrics beside the page, a visualizer, volume, the equalizer.
  - **Mini player**: leave Music while something plays and a small window of its own takes over, dragged
    anywhere and opening where you left it; double-click its cover to go back to Music.
  - Media keys and the system's media controls (Windows' media overlay, MPRIS on Linux) play and pause, skip
    and show what's playing; keyboard shortcuts while Music shows (Space, Ctrl+arrows, Ctrl+F, Ctrl+L...);
    Steam's back and forward arrows go back and forward in Music.
  - **Play soundtrack** on the page of a game whose soundtrack you own.
  - The Music tab's menu on hover, as Store's and Community's have; MusicDeck's settings as a dialog.
  - About 90 KB more of MusicDeck, and nothing that runs in the background: it's drawn while desktop Steam
    shows and taken away when Big Picture or Game Mode does.

## 0.31.1

- **MusicDeck didn't start on some Linux systems** (Bazzite among them) in 0.31.0: the iPhone support for
  Windows loaded a part of Python that Decky's own Python doesn't carry, and the system's copy didn't fit.
  It's loaded only when an iPhone is used on Windows now. The tests now load MusicDeck the way Decky does,
  without those parts, so this can't slip through again.

## 0.31.0

- **Devices on Windows**: everything Local files' Devices does on the Deck now works on a Windows PC too.
  - Music players, USB sticks and card readers show up by their drive letter's name, and eject the way
    "Safely remove" does. A stick keeps the same entry (index, favourites) on Windows as on the Deck.
  - **iPods** are named and identified as on Linux, and sync, covers and all. Their SysInfoExtended comes
    from the iPod over SCSI, as libgpod does it. A Mac-formatted iPod (which Windows can't read) is said, not
    silently missing.
  - **Audio CDs** play and rip, and data CDs of MP3s play.
  - **Phones** set to File transfer: through Windows' own MTP support (a small helper, mtp-helper). As Windows
    doesn't show a phone as folders, its songs are read off it as they're needed, to index them and to play
    them, and up to 2 GB of them are kept on the PC. Copying music to a phone works too.
  - **iPhones and iPod touches**: their synced music, read only, as on Linux. Needs Apple Devices or iTunes
    installed (Windows has no iPhone driver without it), and the iPhone to trust the PC once.
- **Recently deleted on Windows**: what's deleted goes to the Recycle Bin as before, and now Restore in
  MusicDeck puts it back.
- **New music found at once on Windows**, as on the Deck, rather than by the hourly check.
- **An equalizer for each output on Windows**: the speakers, headphones, a Bluetooth device or a TV each
  keep their own, as on the Deck.
- Tried on real hardware so far: a HiBy R4 over MTP (played from, and a song copied off it), and a PC's own
  drives, Recycle Bin and sound output. iPods, CDs and iPhones on Windows not yet.
- A device plugged in within 30 seconds of the Deck starting didn't say how much room it had at first.
- A Local files folder holding a whole drive (or /) no longer stalls MusicDeck while it's watched for new
  music: the first 10,000 folders are watched, and the hourly check covers the rest.

## 0.30.2

- **Burn to CD on Windows**: the Windows zip has cdrskin too, the same libburn talking to the drive through
  libcdio, so a CD drive plugged into a Windows PC burns as on the Deck (gapless, with CD-Text), and the PC
  doesn't sleep meanwhile. Not tried with a real burner yet.

## 0.30.1

- **Burn to CD works out of the box on Linux**: cdrskin now comes with MusicDeck as a static executable for
  x86_64 and ARM64 (with its licence, GPL-2.0-or-later, and where its source is), so no burner needs
  installing. Still not tried with a real burner.

## 0.30.0

- **Burn to CD**: an album, artist or playlist in Local files (Y → Burn to CD) written to a blank CD-R or
  CD-RW as an audio CD, gapless (Session At Once), with CD-Text: the album's and each song's title and
  artist, which car stereos and many players show. MusicDeck checks the disc first (blank, a CD-RW to
  erase, how many minutes it holds) and says what it'll do; the Deck doesn't sleep while it burns, and
  Decky → MusicDeck shows how far it's got. Linux, with a CD drive; burned by cdrskin (libburn's), which
  builds bundle once it's built for them (scripts/build_cdrskin.sh). Not tried with a real burner yet.

## 0.29.0

- **Album covers on the iPod's screen**: songs MusicDeck puts on an iPod show their album's cover there (Now
  Playing, Cover Flow, the album lists), at each size that iPod uses: the photo, 5th/5.5th generation, every
  nano and the classic. The cover is the album's picture on the Deck, else the one in the file, else the
  folder's cover.jpg. Ported from libgpod and checked against it: the same artwork database byte for byte
  for the same songs, and libgpod reads MusicDeck's back. Covers iTunes put there are kept. Needs ffmpeg.
- **Sound Check**: a song's ReplayGain goes on the iPod as its Sound Check, and the Sound Check of songs on an
  iPod levels them when MusicDeck plays them.
- **Renaming an iPod renames the iPod itself**, as iTunes does (its About screen says the new name), rather
  than only MusicDeck's name for it.

## 0.28.3

- **Videos aren't songs**: a phone's camera clips (and other .mp4 or .webm files with a picture in them)
  showed up as songs in Devices and Local files. MusicDeck now looks inside .mp4 and .webm files and keeps
  only audio-only ones (a song downloaded as .mp4 or .webm still plays); videos already in a library leave
  it on its next scan. An iPod's movies, TV shows and music videos are left out too.

## 0.28.2

- The Library entry's Play opens the media center: Steam names a shortcut's launch by its 64-bit game id,
  not its app id, so 0.28.0 missed it; and the media center now opens once Steam has shown the game's page.

## 0.28.0

- **The iPod nano 5th, 6th and 7th generation sync too.** They play from SQLite libraries beside their own
  database, both signed for each iPod; MusicDeck writes both, as libgpod (gtkpod's library) does, checked
  against libgpod's own output table by table. The nano 6th/7th generation's signature comes from a small
  helper built from dstaley/hashab (public domain), checked against its 100 test vectors. A nano 5th
  generation needs to have been synced with iTunes once (its key is in what iTunes signed); it says so if it
  hasn't. Every iPod with a disk mode now syncs. Untested on a real nano so far.
- MusicDeck asks an iPod for its full description (SysInfoExtended) over USB, as libgpod's helper does, and
  keeps it on the iPod: its exact model, FireWire ID and the SQL the newer nanos want run on their libraries.
- Dates on an iPod are its local time: MusicDeck read and wrote them as UTC, hours off.
- **MusicDeck in your Library**: Decky → MusicDeck → More → Show in your Library adds MusicDeck to the Steam
  library (with its own artwork), so it can be started from Big Picture like a game: Play opens the media
  center. Off by default.

## 0.27.0

- **Delete from your library**: Y on a song, album, folder or playlist file in Local files has Delete. It goes
  to the trash, not away: an album's folder goes whole when only its cover and rip logs would be left, and an
  artist's folder left empty is tidied up. Artists aren't deleted whole.
- **Recently deleted**: a Local files tab (while there's something in it) listing what was deleted lately;
  open one and Restore puts it back where it was. It's the desktop's Trash too, for desktop mode.
- **Eject**: Y on a device's card in Devices, or its row in Decky → MusicDeck → Plugged in, ejects it as the
  desktop would (a stick or iPod unmounted and powered down, a phone closed, a CD drive's tray opened).
- **How much room is left**: a device's card says how much space is free on it, its page how much of how
  much, and a copy that won't fit says so before it starts.
- **A device's page** leads with its Songs, Artists and playlists, then its albums.
- **Data discs**: a CD or DVD of MP3s (one burned for a car stereo) plays from Devices, as a player.
- **iPhone and iPod touch**: once unlocked and trusted, the music synced to one plays from Devices (read
  only; Apple Music downloads are protected and don't). A toast says to tap Trust when it's needed.
- **MusicDeck playlists** of Local files can be copied to a device too.
- **Mac-formatted iPods** say exactly what to do to sync them (turn their journal off on a Mac, or restore
  them for Windows), rather than only that they can't.
- A CD's album page shows its rip's progress, and offers Rip again once it's done.

## 0.26.0

- **iPods**: an iPod plugged in (any with a disk mode: classic, nano, shuffle, mini, video, photo) shows in
  Local files → Devices by the name it has in iTunes and its model, read from its own library in a second.
  Copying music to it puts it into that library as iTunes did, so the iPod's own menus play it: FLAC and
  other formats it can't play are converted (to Apple Lossless, or AAC), playlists stay playlists, and plays
  and On-The-Go playlists made on the iPod are kept. The iPod classic's and the nano 3G/4G's signed library is
  signed for each iPod. A nano 5G, 6G or 7G, or a Mac-formatted iPod, plays but isn't synced (Decky →
  MusicDeck says "play only"). See docs/IPOD.md.
- **Remove from a device**: Y on something on a plugged-in player, phone or iPod has Remove from <device>
  (asked first).
- **No more flashing covers**: a card without a picture (a player's album with no cover) flickered between
  a broken-image icon and its own icon each time it was drawn (L1/R1 between tabs); it shows its icon
  straight away now. Covers appear once they've loaded.
- **Now Playing at rest**: the record's label no longer shows a broken image when what played last has no
  cover; it stays blue.

## 0.25.1

- Devices come first on the media center's Home too, and Browse's Devices row shows them.
- A device that was the live source before 0.25.0 hands over to Local files when MusicDeck starts.

## 0.25.0

- **Devices are part of Local files**: music players, phones, USB sticks and CDs now show in Local files,
  in a Devices tab (and first on Home) while they're plugged in, instead of being sources of their own. Open
  one to see its albums; its songs play, queue and go in playlists with the rest of your library. Local
  files' search finds what's on them too, under "On <device>". Decky → MusicDeck lists them under Plugged
  in, where a player or phone can be renamed.
- **Copy music to a player, and back**: Y on an album, artist, playlist or song in Local files has "Copy
  to <device>" for each player, phone or stick plugged in; on something of a device's, "Copy to this
  Deck". Files are copied as they are, into Artist/Album folders (on a player, in its Music folder);
  what's already there is left alone. Progress shows on Local files in Decky → MusicDeck, and a toast says
  when it's done.
- **Local files when there's none**: plugging in a player, phone or CD with no Local files source adds one
  on ~/Music, so devices and CD rips have a library to go to.
- Rip a CD from its card in Devices: its album page has Rip to Local files, as before.

## 0.24.1

- **Plugged-in phones and players have readable names**: their serial number, URL escapes and underscores
  are gone ("HiBy_TRINKET-IOT-IDP__SN%3AAC53E709_ac53e709" is now "HiBy TRINKET-IOT-IDP").
- **Rename a plugged-in device**: some firmware names a player after its circuit board, not its model. In
  Decky → MusicDeck, its menu has Rename; the name stays for the next time it's plugged in.

## 0.24.0

- **Audio CDs**: put a CD in a drive plugged into the device and it shows up in Decky → MusicDeck as a
  source: pick it and play it, seeking included. MusicBrainz names the album and its songs and brings the
  cover (kept on the device, so a CD played before is named straight away, offline too); a CD it doesn't
  know plays as Track 1, Track 2...
- **Rip a CD to Local files**: the CD's album page has Rip to Local files, which saves it as FLAC, tagged
  and with its cover, into your first Local files folder under Artist/Album, where Local files picks it up.
  Needs ffmpeg (Bazzite has it) and a Local files source.

## 0.23.0

- **Plug in a music player**: an MP3 player, an iPod in disk mode, a USB stick, or a phone set to File
  transfer appears in Decky → MusicDeck as a source while it's plugged in; pick it and play what's on it.
  MusicDeck only reads it. When it's unplugged, the source you had before comes back.

## 0.22.0

- **SoulSync**: play the library of the SoulSync you run. Add it with its address and an admin API key
  (SoulSync: Settings > Advanced > SoulSync API): recently added albums and songs on Home, Albums, Artists,
  Recently added, and search; songs play from SoulSync with seeking, and playlists made in MusicDeck can be
  downloaded for offline like your other servers'.

## 0.21.2

- **Now Playing at rest**: with nothing playing it's the player, idle: a turntable where the cover goes, with
  the cover of what you played last on its label, the visualizer's resting dots, the seek bar empty and the
  controls dimmed but Play, which starts that again.
- **A queue survives a restart whichever source it's from**: music played from another source while looking
  at it in the media center was dropped when MusicDeck restarted (only the live source's came back).

## 0.21.1

- **Empty pages can be left**: B did nothing on an empty playlist (or any page with nothing on it, or one that
  couldn't load) in the media center, as nothing on it could take the controller. It rests on the page's
  "Empty" now, and B goes back.
- **Now Playing with nothing playing** looks like Now Playing: a record where the cover goes, "Nothing
  playing" and the source, Home, Browse and Search, and the source's Jump back in to start again in one press.
- Big screens: the media center finds Steam's hint bar on every build of Steam (the measured fit from 0.21.0
  could fall back to the Steam Deck's proportions).

## 0.21.0

- **Every genre, mood, country and year is a row on Browse**, each ending in its own Show all, however many
  there are: SoundCloud's 35 genres showed 15 rows and a lone "All genres" square to click into. Radio's
  countries and languages, the Live Music Archive's years and local files' genres and decades were squares
  to pick from; they're rows now too (each loads as you reach it).
- **SoundCloud's charts have pictures**: SoundCloud's own artwork for each (its top track's), not a grey
  square.
- **More search results**: SoundCloud asked for 40 songs (fewer showed, once the ones that can't be played
  signed out were left out), 6 artists and 8 playlists; Qobuz for 30 of each and 8 artists. Now up to 120
  songs, 16 artists, 16 albums and 24 playlists on SoundCloud, and 60 of each and 16 artists on Qobuz; long
  rows end in Show all.
- **Big screens**: the media center fits between Steam's own header and hint bar as Steam draws them,
  rather than a share of the screen that only matched the Steam Deck's; on 2560x1600 the now-playing bar
  floated above the hint bar.

## 0.20.2

- **Windows: YouTube Music works without installing Python.** It said Python 3.10 wasn't installed: the
  Windows zip now brings python.org's embeddable Python (3.14.8) for it, and only falls back to an installed
  Python without it.

## 0.20.1

- **Windows: Qobuz's library loads.** An album released before 1970 stopped it with "[Errno 22] Invalid
  argument", as Windows can't read dates before 1970 the way Linux can; podcast and audiobook dates had the
  same weakness. Dates are now worked out in a way that holds on every system.
- One zip per system: `-linux-x86_64`, `-linux-arm64` and `-windows-x86_64`.

## 0.20.0

**ARM and Windows, experimental.** Decky Loader 3.3 runs on ARM64 Linux and Windows, and so does MusicDeck now:
- **ARM64 Linux** (Armada on ARM handhelds, DroidDeck on Android, Steam Frame): the zip carries ARM64 builds of
  the Spotify helper and YouTube's JavaScript engine, and MusicDeck picks the ones for the device it's on.
- **Windows** (Decky's Windows build, e.g. through Playhub; Windows on Arm too): Windows builds of both helpers,
  started without console windows; YouTube runs in an installed Python 3.10 or newer (python.org); the Steam
  soundtracks source finds Steam's folder from the registry; editing tags waits a moment for a file Windows
  has open, then says it's in use.
- The Steam Deck and other x86_64 SteamOS-like systems work as before.

## 0.19.0

- **A new visualizer in the Quick Access panel:** the song's cover as a record, turning while the music plays
  and slowing to a stop when it pauses, with the music as spokes round it (the bass at the top) in the
  cover's own colour, and a glow behind that swells with the bass. It fills the page instead of a thin line
  across it.
- The visualizers read the music over a wider range, so loud bass no longer holds the low end at full.

## 0.18.1

- Songs say nothing about their lyrics any more: no "Lyrics aren't available right now" under the visualizer
  (Quick Access and full screen), and no "Looking for lyrics…" or "Couldn't load lyrics" in the media center.
  The lyrics show when there are some, the visualizer when there aren't.

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
