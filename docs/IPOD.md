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
| iPod nano 5th generation | yes | not yet | hash72 + SQLite databases (see below) |
| iPod nano 6th and 7th generation | yes | not yet | hashAB + SQLite databases (see below) |
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
   sorted lists again, adds a playlist when a playlist was copied.
5. Writes the new database beside the old one and moves it over it, signed for this iPod. The previous
   one is kept on the Deck (`ipod-<id>/iTunesDB.previous` in the plugin's runtime folder). A shuffle gets
   its `iTunesSD` too.

Removing songs takes them out of the database and every playlist, then deletes their files.

## The options, explored (October 2026)

**Mac-formatted (HFS+) iPods.** Linux's hfsplus driver writes only volumes whose journal is off, and
iTunes leaves the journal on. Turning it off, or mounting with `force`, needs the raw disk (root), which a
Decky plugin doesn't have and shouldn't take. So MusicDeck plays them and says what to do: on a Mac, Disk
Utility → the iPod → File → Disable Journaling (`diskutil disableJournal /Volumes/<iPod>`), after which Linux
writes it; or restore the iPod with iTunes for Windows, which formats it FAT32.

**The nano 5th-7th generation: possible, next.** They read SQLite libraries (`iTunes Library.itlp`: Library,
Locations, Dynamic, Extras, Genius) beside a compressed iTunesCDB, and both are signed:
- nano 5G: hash72, an AES signature whose key can be recovered from any database iTunes signed for that
  iPod (libgpod's HashInfo): every nano 5G that was ever synced with iTunes has one.
- nano 6G/7G: hashAB. [dstaley/hashab](https://github.com/dstaley/hashab) is a public-domain C
  implementation (reverse engineered from the nano 6G's firmware, checked against the 2010 library on 10,000
  inputs, with 100 test vectors). It would build into MusicDeck's per-platform helpers (`bin/<platform>/`) as
  the Spotify helper does.
- The SQLite libraries: libgpod's itdb_sqlite.c, and iOpenPodv2's MIT-licensed writer
  ([chrismheiser/iOpenPodv2](https://github.com/chrismheiser/iOpenPodv2)), are the references; Decky's Python
  has sqlite3.
This is a large piece on its own and unforgiving without a nano to try it on (a wrong signature and the nano
asks to be restored), so it's the next step rather than part of this one.

**iPhone and iPod touch.** No disk mode, but Apple's AFC service (what photo import uses) shows the music
an iTunes/Finder sync put on them; GVfs on Bazzite mounts it once the phone trusts the computer. MusicDeck
plays that (read only). Putting music *on* one needs Apple's sync protocol (and, on current iOS, its own
signed databases), which nothing open implements; Apple Music downloads are FairPlay-protected.

**The car-stereo way (iAP).** Car stereos browse and play iPods and iPhones over Apple's accessory protocol,
and from iPhones (and iAP2) that requires Apple's authentication chip in the accessory, sold only under its
MFi licence. Not something software can do.

## Not done yet

- **Artwork on the iPod's screen.** Covers live in a separate database (ArtworkDB, with thumbnails in each
  model's own sizes). Songs MusicDeck adds show no cover on the iPod; songs iTunes put there keep theirs.
  MusicDeck shows covers for everything itself. iOpenPodv2's ArtworkDB writer (MIT) is a reference.
- **Syncing the nano 5th-7th generation** (above).

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

If the iPod shows no music after a sync, its database's signature wasn't accepted: copy
`iTunesDB.previous` from the Deck back to `iPod_Control/iTunes/iTunesDB` to undo the sync, and note the
model (Settings → About on the iPod) and what `iPod_Control/Device/SysInfo` says.
