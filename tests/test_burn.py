"""
Burning audio CDs (musicdeck/burn.py, Local files' burn in services/shelf.py) without a drive: what
cdrskin prints read (as its source prints it: cdrskin.c, libburn 1.5.8), the CD-Text sheet, songs made
into CD audio (with ffmpeg, when it's here), and a burn planned and run with cdrskin stood in for.

    python tests/test_burn.py
"""
import asyncio
import logging
import os
import struct
import sys
import tempfile
import types
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "py_modules")]
sys.modules.setdefault("decky", types.SimpleNamespace(logger=logging.getLogger("decky")))

from musicdeck import burn, cd  # noqa: E402
from musicdeck.services.base import ServiceError  # noqa: E402
from musicdeck.services.shelf import LocalLibrary  # noqa: E402

# what `cdrskin dev=/dev/sr0 -atip` prints for a blank 80-minute CD-R, and for a CD-RW with music on it
BLANK = """cdrskin 1.5.8 : limited cdrecord compatibility wrapper for libburn
cdrskin: status 1 burn_disc_blank "The drive holds a blank disc"
scsidev: '/dev/sr0'
Device type    : Removable CD-ROM
Current: CD-R
ATIP info from disk:
  Is not erasable
  ATIP start of lead in:  -11849 (97:22/01)
  ATIP start of lead out: 359846 (79:59/71)
  1T speed low:  0 1T speed high: 48
"""
FULL_RW = """cdrskin: status 2 burn_disc_full "There is a disc with data on it in the drive"
Current: CD-RW
ATIP info from disk:
  Is erasable
  ATIP start of lead out: 359846 (79:59/71)
"""
NONE = """cdrskin: status 0 BURN_DISC_EMPTY "There is no disc at all in the drive"
Current: none
"""


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print("  ok -", message)


def make_wav(path: str, seconds: float) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\x01\x00\x02\x00" * int(44100 * seconds))


def reading() -> None:
    print("what cdrskin says")
    blank = burn.parse_atip(BLANK)
    check(blank.status == "blank" and blank.media == "CD-R" and not blank.erasable and blank.frames == 359846
          and blank.minutes == 79 and blank.usable, f"a blank CD-R, its 80 minutes: {blank}")
    rw = burn.parse_atip(FULL_RW)
    check(rw.status == "full" and rw.erasable and rw.usable, f"a CD-RW with music on it: usable, once erased: {rw}")
    check(not burn.parse_atip(FULL_RW.replace("Is erasable", "Is not erasable").replace("CD-RW", "CD-R")).usable,
          "a CD-R with music on it isn't")
    none = burn.parse_atip(NONE)
    check(none.status == "empty" and not none.usable, "no disc")
    check(burn.parse_progress("Track 03:   12 of   45 MB written (fifo 100%) [buf  99%]   8.1x.") == (3, 12, 45)
          and burn.parse_progress("Track 01:    4 MB written") == (1, 4, 0)
          and burn.parse_progress("cdrskin: burning") is None, "its progress lines")
    code, out = burn._run([sys.executable, "-c", "import sys; sys.stdout.write('Track 01: 1 of 2 MB written\\r"
                           "Track 01: 2 of 2 MB written\\rcdrskin: FATAL : Drive is busy\\n'); sys.exit(3)"],
                          lambda line: None)
    check(code == 3 and burn._failure(out) == "Drive is busy", f"run, and why it stopped: {out!r}")


def cdtext() -> None:
    print("CD-Text")
    check(burn.latin1("Beyoncé") == "Beyoncé" and burn.latin1("Łódź") == "Lódz" and burn.latin1("東京") == ""
          and len(burn.latin1("x" * 200)) == burn.CDTEXT_FIELD, "Latin-1, as CD-Text's 8859 holds it")
    sheet = burn.cdtext_sheet("Abbey Road", "The Beatles", [{"title": "Come Together"}, {"title": "Something",
                                                                                          "artist": "George"}])
    lines = sheet.splitlines()
    check(lines[0] == "Input Sheet Version = 0.7T" and "Album Title = Abbey Road" in lines
          and "Track 01 Title = Come Together" in lines and "Track 01 Artist = The Beatles" in lines
          and "Track 02 Artist = George" in lines and "Last Track Number = 2" in lines,
          "a Sony input sheet: the album, each song")
    check(burn.frames_needed([1.0, 0.5]) == 150 + 75 + 38, "a CD's frames: each song rounded up, and the lead-in")


async def planned() -> None:
    print("a CD planned and burned (cdrskin stood in for)")
    music = tempfile.mkdtemp()
    album = os.path.join(music, "Band", "Album")
    os.makedirs(album)
    for n in (1, 2):
        make_wav(os.path.join(album, f"0{n} Song {n}.wav"), 1.0 + n / 10)
    runtime = tempfile.mkdtemp()
    lib = LocalLibrary("burntest", {"paths": [music]}, runtime)
    await lib.start()
    await lib.scan_task
    ids = list(lib.tracks)
    disc = {"text": BLANK}
    calls = []
    burn.burner = lambda runtime_dir=None: "cdrskin"
    burn.probe = lambda cdrskin, drive: burn.parse_atip(disc["text"])
    cd.drives = lambda: ["/dev/sr0"]
    if not __import__("shutil").which("ffmpeg"):
        print("  (no ffmpeg here: burning not tried)")
        await lib.stop()
        return

    def fake_burn(cdrskin, drive, wavs, sheet, progress):
        calls.append(("burn", drive, [open(w, "rb").read() for w in wavs], open(sheet, encoding="latin-1").read()))
        progress(1, 0, 0)
        progress(2, 0, 0)

    burn.burn = fake_burn
    burn.erase = lambda cdrskin, drive: calls.append(("erase", drive))
    plan = await lib.burn_plan("playlist", "x", ids, "Road trip")
    check(plan["songs"] == 2 and plan["minutes"] == 1 and plan["capacity"] == 79 and not plan["erase"]
          and plan["title"] == "Road trip" and plan["artist"] == "", f"the plan: {plan}")
    message = await lib.burn("playlist", "x", ids, "Road trip")
    await lib.burning
    check(message.startswith("Burning Road trip to CD") and lib.burn_progress["running"] is False
          and not lib.burn_progress["error"] and calls[0][0] == "burn", f"burned: {lib.burn_progress}")
    wavs = calls[0][2]
    sizes = [struct.unpack_from("<I", w, 40)[0] for w in wavs]
    check(all(s % burn.FRAME == 0 and len(w) == 44 + s for s, w in zip(sizes, wavs))
          and sizes[0] == -(-int(44100 * 1.1) * 4 // burn.FRAME) * burn.FRAME,
          f"each song CD audio, padded to whole frames: {sizes}")
    check("Album Title = Road trip" in calls[0][3] and "Track 02 Title = " in calls[0][3], "with its CD-Text")
    check(not [n for n in os.listdir(runtime) if n.startswith("burn-")], "its CD audio cleaned up after")
    disc["text"] = FULL_RW
    calls.clear()
    await lib.burn("playlist", "x", ids, "Road trip")
    await lib.burning
    check([c[0] for c in calls] == ["erase", "burn"], "a CD-RW with music on it erased first")
    disc["text"] = NONE
    try:
        await lib.burn_plan("playlist", "x", ids, "Road trip")
        check(False, "no disc, no burn")
    except ServiceError as e:
        check("blank CD" in str(e), f"no disc: it says so: {e}")
    disc["text"] = BLANK.replace("359846", "100")
    try:
        await lib.burn_plan("playlist", "x", ids, "Road trip")
        check(False, "too long")
    except ServiceError as e:
        check("of music" in str(e), f"more than the CD holds: {e}")
    await lib.stop()


if __name__ == "__main__":
    reading()
    cdtext()
    asyncio.run(planned())
    print("all burning checks passed")
