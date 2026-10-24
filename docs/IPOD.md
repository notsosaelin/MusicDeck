# iPods

MusicDeck plays and syncs iPods with Apple's own software on them, the way iTunes did, without iTunes.
This page is what that rests on: which iPods, how, and what's still out of reach. The code is
`musicdeck/itunesdb.py` (the database format), `musicdeck/ipod.py` (identifying and syncing) and
`musicdeck/services/ipod.py` (an iPod in Local files' Devices).

## Why an iPod isn't a USB stick

Apple's firmware doesn't look at files. It plays what its database, `iPod_Control/iTunes/iTunesDB`,
lists: each song's names, its file (`iPod_Control/Music/F00/ABCD.mp3`, under a made-up name), playlists,
and the sorted lists the menus show (Artists, Albums, Songs...). Copying music onto an iPod means adding it
to that database. From the iPod classic and the 3rd-generation nano on, the database is also signed
(an HMAC keyed from the iPod's FireWire ID); an iPod that finds the signature wrong shows no music.

libgpod, the library behind gtkpod, Rhythmbox's and Strawberry's iPod support, is the reference for all
of it. MusicDeck reimplements what it needs in Python (Decky plugins can't count on libgpod being
installed) and is checked against libgpod itself: `scripts/libgpod_check` writes and reads databases
with the real libgpod, and the databases it wrote are in `tests/fixtures/ipod` for `tests/test_ipod.py`.

## Which iPods

| iPod | Plays from it | Syncs to it | How it's signed |
|---|---|---|---|
| iPod 1st-4th generation, photo, mini | yes | yes | not signed |
| iPod 5th/5.5th generation ("video") | yes | yes | not signed |
| iPod nano 1st and 2nd generation | yes | yes | not signed |
| iPod classic (all three) | yes | yes | hash58, from the FireWire ID |
| iPod nano 3rd and 4th generation | yes | yes | hash58 |
| iPod shuffle 1st-4th generation | yes | yes | not signed; reads iTunesSD (written too) |
| iPod nano 5th generation | yes | yes, once synced with iTunes | hash72 (its key from iTunes' signature) + SQLite libraries |
| iPod nano 6th and 7th generation | yes | yes | hashAB (the hashab helper) + SQLite libraries |
| iPod touch, iPhone | yes, its synced music | no | AFC (GVfs), read only; no disk mode |
| Any iPod running Rockbox | yes | yes | none: Rockbox plays files, so it's a player like any other |

"Plays from it" goes through its database too (a second, where reading thousands of files' tags over USB
would take minutes).

## How MusicDeck tells which one it is

In order: `iPod_Control/Device/SysInfoExtended` (its `DBVersion` says how to sign), `SysInfo`'s
`ModelNumStr` (`xB147` is an 80 GB iPod classic; libgpod's model table), the USB product id (sysfs), and
how the database already on it is signed. The FireWire ID comes from SysInfo(Extended), else the USB
serial number, which is the same thing (libgpod's README.SysInfo). Neither needs root.

When it can't tell how an iPod wants its database signed, MusicDeck doesn't change it.

## What a sync does

1. Reads the database as it is on the iPod now.
2. Folds in what the iPod recorded since the last sync: plays, ratings and bookmarks (`Play Counts`) and
   On-The-Go playlists (`OTGPlaylistInfo`), so nothing is lost or counted twice.
3. Copies each song to `iPod_Control/Music/Fnn/ABCD.ext`. Formats iPods can't play are converted with
   ffmpeg: FLAC and other lossless to Apple Lossless at CD quality (AAC for a 1st/2nd generation shuffle,
   which has no Apple Lossless), Ogg and Opus to 256 kbps AAC. MP3, AAC, Apple Lossless, WAV and AIFF go as
   they are.
4. Adds them to the database (and the albums and artists lists), makes the master playlist and its
   sorted lists again, adds a playlist when a playlist was copied. A song's ReplayGain becomes its Sound
   Check (and an iPod song's Sound Check is ReplayGain when MusicDeck plays it).
5. Gives each new song its album's cover on the iPod's screen (below).
6. Writes the new database beside the old one and moves it over it, signed for this iPod. The previous
   one is kept on the Deck (`ipod-<id>/iTunesDB.previous` in the plugin's runtime folder, and
   `ArtworkDB.previous`). A shuffle gets its `iTunesSD` too.

Removing songs takes them out of the database and every playlist, then deletes their files; a cover no
song uses any more leaves the ArtworkDB.

Renaming an iPod (Y on it in Devices → Rename) renames the iPod itself, as iTunes does: its main
playlist's name, which its About screen shows too.

## Covers on the iPod's screen

`ipod_artwork.py`, a port of libgpod's ArtworkDB and .ithmb writer:

- Each iPod with a colour screen shows covers at sizes of its own: SysInfoExtended's AlbumArt list when it
  has one (the nano 6G/7G only say it there), else libgpod's table (the photo, 5th/5.5th generation, nano
  1G-5G, classic). RGB565 pixels, centred in each size on its background colour.
- A song's cover: its album's picture on the Deck (set or found), else the one in its file, else the
  folder's cover.jpg. Scaled with ffmpeg (no image library in Decky's Python); no ffmpeg, no covers.
- iPods that share covers (nano 3G on, classic) get one per album; older ones one per song, as libgpod does.
- The covers iTunes put there are kept as they are: new pictures go at the ends of the `F<size>_1.ithmb`
  files.
- Checked against libgpod: for the same songs and cover, the same ArtworkDB byte for byte (but the date it
  was made), the same files at the same sizes and offsets, and libgpod reads ours back at every size. The
  pixels differ a little (ffmpeg's scaling, not gdk-pixbuf's).

## The options, explored (October 2026)

**Mac-formatted (HFS+) iPods.** Linux's hfsplus driver writes only volumes whose journal is off, and
iTunes leaves the journal on. Turning it off, or mounting with `force`, needs the raw disk (root), which a
Decky plugin doesn't have and shouldn't take. So MusicDeck plays them and says what to do: on a Mac, Disk
Utility → the iPod → File → Disable Journaling (`diskutil disableJournal /Volumes/<iPod>`), after which Linux
writes it; or restore the iPod with iTunes for Windows, which formats it FAT32.

**The nano 5th-7th generation: done (0.28.0), untested on a real nano.** They play from SQLite libraries in
`iPod_Control/iTunes/iTunes Library.itlp` (Library, Locations, Dynamic, Extras, Genius) beside a compressed
iTunesCDB, and both are signed. MusicDeck writes them as libgpod does:
- `itunesdb.py`: the iTunesCDB (its body zlib-compressed after the header, flagged at 0xA8), signed with
  hash72 (nano 5G: AES-CBC, `aes.py`, with a key recovered from any database or checksum file iTunes signed
  for that iPod, kept as libgpod's `iPod_Control/Device/HashInfo`) or hashAB (nano 6G/7G); an empty iTunesDB
  beside it.
- `ipod_sqlite.py`: the five libraries (tables from `ipod_schema.py`), ported from libgpod's itdb_sqlite.c,
  and `Locations.itdb.cbk` (the SHA-1 of every 1 KiB block of Locations.itdb, the SHA-1 of those, signed).
  The iPod's own SQL for them (SysInfoExtended's SQLMusicLibraryPostProcessCommands, with its sort-key
  functions) runs when the iPod gives it.
- hashAB: [dstaley/hashab](https://github.com/dstaley/hashab) (public domain, reverse engineered from the
  nano 6G's firmware, checked against the 2010 library on 10,000 inputs), vendored in `backend/hashab` and
  built into `bin/<platform>/hashab` (`scripts/build_hashab.sh`, which checks its 100 test vectors).
- SysInfoExtended is asked of the iPod over USB (a vendor control request, libgpod's ipod-usb.c) and kept on
  it, which needs the iPod's USB device to be the user's (it is for media players under systemd's uaccess).
Checked: `scripts/libgpod_check` has libgpod write for a nano 5G and compares every table, the hash72 and
the checksum file; `tests/test_ipod.py` does the same against libgpod's output saved in `tests/fixtures/ipod`.
Not checked: a real nano, the hashAB value at 0x30 (libgpod writes 3, iOpenPod 4; MusicDeck keeps what
iTunes wrote, 4 for a new library), and whether the nanos need the post-process SQL (iOpenPod doesn't run it).

**iPhone and iPod touch.** No disk mode, but Apple's AFC service (what photo import uses) shows the music
an iTunes/Finder sync put on them; GVfs on Bazzite mounts it once the phone trusts the computer. MusicDeck
plays that (read only). Putting music *on* one needs Apple's sync protocol (and, on current iOS, its own
signed databases), which nothing open implements; Apple Music downloads are FairPlay-protected.

**The car-stereo way (iAP).** Car stereos browse and play iPods and iPhones over Apple's accessory protocol,
and from iPhones (and iAP2) that requires Apple's authentication chip in the accessory, sold only under its
MFi licence. Not something software can do.

## Not done yet

- **Photos and videos.** Only music is synced (no Photo Database, no movies).
- **Space left by removed covers.** A cover taken out of the ArtworkDB leaves its pictures in the .ithmb
  files (libgpod packs them; MusicDeck doesn't yet). They're a few hundred KB per album.

## Testing with a real iPod

What to look at, in order:

1. Plug it in (disk mode: it shows up as a drive). Local files → Devices should list it by the name it
   has in iTunes, with its model ("iPod classic · 3,412 songs"). Its songs should play.
2. Decky → MusicDeck → Plugged in: its row shouldn't say "play only" (for a syncable model).
3. Copy one album to it (Y on the album → Copy to <iPod>). Eject it properly (Steam's or the file
   manager's eject) before unplugging, then look on the iPod: Music → Albums should have it, and it
   should play. Then Artists and Songs (the sorted lists).
4. Copy a FLAC album: it should arrive as Apple Lossless and play.
5. Copy a playlist: it should be under Playlists.
6. Remove a song (Y → Remove from <iPod>), eject, check it's gone from the iPod.
7. Play something on the iPod, plug it in again, copy something: the play count on the iPod should
   still be there afterwards (Play Counts folded in).
8. Covers: the copied album should show its cover in Now Playing and Cover Flow on the iPod.
9. Rename it in Devices: the iPod's Settings → About should say the new name.

If the iPod shows no music after a sync, its database's signature wasn't accepted: copy
`iTunesDB.previous` from the Deck back to `iPod_Control/iTunes/iTunesDB` to undo the sync, and note the
model (Settings → About on the iPod) and what `iPod_Control/Device/SysInfo` says.
