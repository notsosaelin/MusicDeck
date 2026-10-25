"""
Windows' side of devices, CDs and iPods (musicdeck/winio.py, and where devices.py, cd.py and ipod.py use
it): what Windows answers parsed (a CD's table of contents, a drive's descriptor, a USB instance id, an
iPod's vital product data), and what's plugged in made into devices, from answers made up here; so it
runs anywhere. On Windows it also asks this PC's own drives, which must not fail or show internal drives.

    python tests/test_windows.py
"""
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "py_modules")]

from musicdeck import cd, devices, winio  # noqa: E402


def check(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print(f"  ok - {message}")


def msf(lba: int) -> bytes:
    frames = lba + 150
    return bytes([0, frames // 4500, frames // 75 % 60, frames % 75])


def toc_bytes(entries: list[tuple[int, int, bool]]) -> bytes:
    """A CDROM_TOC as Windows returns it."""
    body = b"".join(bytes([0, 0x10 | (0 if audio else 0x04), n, 0]) + msf(lba) for n, lba, audio in entries)
    return struct.pack(">HBB", 2 + len(body), entries[0][0], entries[-2][0]) + body


def parsing() -> None:
    print("what Windows answers")
    entries = [(1, 0, True), (2, 13350, True), (3, 30000, False), (0xAA, 45000, False)]
    toc = winio.parse_toc(toc_bytes(entries))
    check(toc == entries, f"a CD's table of contents, in MSF, as LBA ({toc})")
    check(cd.disc_id(toc) == cd.disc_id(entries), "the same disc id as Linux's ioctls give")
    check(winio.parse_toc(b"\0\x02") == [], "no table of contents: nothing")

    vendor, product, serial = b"Apple\0", b"iPod\0", b"000A2700138A422D\0"
    head = struct.calcsize("<IIBBBBIIIIIII")
    desc = struct.pack("<IIBBBBIIIIIII", 1, 0, 0, 0, 1, 0, head, head + len(vendor), 0,
                       head + len(vendor) + len(product), winio.BUS_USB, 0, 0) + vendor + product + serial
    parsed = winio.parse_device_descriptor(desc)
    check(parsed == {"bus": winio.BUS_USB, "removable": True, "vendor": "Apple", "product": "iPod",
                     "serial": "000A2700138A422D"}, f"a drive's descriptor ({parsed})")

    check(winio.parse_usb_id(r"USB\VID_05AC&PID_1261\000A2700138A422D") == (0x05AC, 0x1261, "000A2700138A422D"),
          "an iPod classic's USB instance id")
    check(winio.parse_usb_id(r"USB\VID_05E3&PID_0749\6&2c1f3b5&0&3") == (0x05E3, 0x0749, ""),
          "a serial Windows made up isn't one")
    check(winio.parse_usb_id(r"USBSTOR\DISK&VEN_APPLE") is None, "not a USB device")

    xml = '<?xml version="1.0"?><plist><dict><key>FireWireGUID</key><string>000A2700138A422D</string></dict></plist>'
    pieces = [xml[i:i + 200].encode() for i in range(0, len(xml), 200)]
    pages = {0xC0: bytes([0, 0xC0, 0, 2 + len(pieces), 0xC0, 0xC1, *range(0xC2, 0xC2 + len(pieces))])}
    for i, piece in enumerate(pieces):
        pages[0xC2 + i] = bytes([0, 0xC2 + i, 0, len(piece)]) + piece
    check(winio.sysinfo_extended_pages(pages.get) == xml, "an iPod's SysInfoExtended from its vital product data")
    check(winio.sysinfo_extended_pages(lambda page: None) == "", "not an iPod: nothing")


def plugged_in() -> None:
    print("what's plugged in")
    vol = {"label": "WALKMAN", "serial": "1234-ABCD", "fs": "FAT32", "read_only": False}
    drives = [
        {"letter": "C", "type": winio.DRIVE_FIXED, "volume": {**vol, "label": "", "serial": "98B9-CC9A", "fs": "NTFS"},
         "storage": {"bus": 17, "vendor": "NVMe", "product": "CT4000P3PSSD8"}},
        {"letter": "E", "type": winio.DRIVE_REMOVABLE, "volume": vol,
         "storage": {"bus": winio.BUS_USB, "vendor": "Sony", "product": "WALKMAN"}},
        {"letter": "F", "type": winio.DRIVE_FIXED, "volume": {**vol, "label": "", "serial": "5555-0000", "fs": "exFAT"},
         "storage": {"bus": winio.BUS_USB, "vendor": "Samsung", "product": "Portable_SSD_T7"}},
        {"letter": "G", "type": winio.DRIVE_REMOVABLE, "volume": None,
         "storage": {"bus": winio.BUS_USB, "vendor": "Generic", "product": "MassStorageClass"}},
        {"letter": "H", "type": winio.DRIVE_CDROM, "volume": {**vol, "label": "MP3 CD", "serial": "0000-1111", "fs": "CDFS"},
         "storage": {}},
        {"letter": "I", "type": winio.DRIVE_CDROM, "volume": {**vol, "label": "Audio CD", "fs": "CDFS"}, "storage": {},
         "cda_only": True},
        {"letter": "J", "type": winio.DRIVE_REMOVABLE, "volume": {**vol, "label": "IPOD", "serial": "AAAA-BBBB"},
         "storage": {"bus": winio.BUS_USB, "vendor": "Apple", "product": "iPod"}},
    ]
    found = {d["mount"][0]: d for d in devices.windows_storage(drives, lambda root: root == "J:\\")}
    check(sorted(found) == ["E", "F", "H", "J"], f"USB drives and data discs; not internal drives, empty slots "
          f"or audio CDs ({sorted(found)})")
    check(found["E"]["name"] == "WALKMAN" and found["E"]["kind"] == "usb" and found["E"]["paths"] == ["E:\\"],
          "a player by its volume's label")
    linux = devices.usb_storage("/dev/sdb1 /run/media/deck/WALKMAN vfat rw 0 0", "deck", {"/dev/sdb1": "1234-ABCD"})
    check(found["E"]["id"] == linux[0]["id"], "the same id it has on Linux (its volume serial is its FAT UUID)")
    check(found["F"]["name"] == "Samsung Portable SSD T7", "no label: the drive's own name")
    check(found["H"]["kind"] == "disc" and found["H"]["read_only"], "a data CD plays, read only")
    check(found["J"]["kind"] == "ipod" and found["J"]["source"] == "\\\\.\\J:", "an iPod, by the drive it's on")
    hfs = [{"letter": "K", "type": winio.DRIVE_REMOVABLE, "volume": None,
            "storage": {"bus": winio.BUS_USB, "vendor": "Apple", "product": "iPod"}}]
    check(devices.unreadable_ipods(drives + hfs) == ["K"], "a Mac-formatted iPod (no volume) is noticed")


def recycle_bin() -> None:
    print("the Recycle Bin")
    import tempfile
    from musicdeck import trash

    def info_v2(path: str) -> bytes:
        return struct.pack("<qqqI", 2, 1234, 0, len(path) + 1) + (path + "\0").encode("utf-16-le")

    def info_v1(path: str) -> bytes:
        return struct.pack("<qqq", 1, 1234, 0) + (path + "\0").encode("utf-16-le").ljust(520, b"\0")
    song = r"D:\Music\Album\01 Song.flac"
    check(trash.recycle_info_path(info_v2(song)) == song, "where a $I (Windows 10) says it was")
    check(trash.recycle_info_path(info_v1(song)) == song, "where a $I (Windows 7) says it was")
    check(trash.recycle_info_path(b"junk") == "", "not a $I")
    with tempfile.TemporaryDirectory() as root:
        sid = os.path.join(root, "S-1-5-21-1")
        os.makedirs(sid)
        for name, said, when in (("$IAAAAAA.flac", song, 100), ("$IBBBBBB.flac", song, 200),
                                 ("$ICCCCCC.flac", r"D:\Music\Other.flac", 300)):
            with open(os.path.join(sid, name), "wb") as f:
                f.write(info_v2(said))
            os.utime(os.path.join(sid, name), (when, when))
        trashed, info = trash.recycled(song.lower(), root)
        check(os.path.basename(trashed) == "$RBBBBBB.flac" and os.path.basename(info) == "$IBBBBBB.flac",
              "what was recycled from a path: the newest $I that says so, its $R beside it")
        check(trash.recycled(r"D:\Nowhere.flac", root) == ("", ""), "nothing recycled from there")


def watching() -> None:
    print("noticing new music")
    from musicdeck import watch

    def record(action: int, name: str, last: bool = False) -> bytes:
        raw = name.encode("utf-16-le")
        size = (12 + len(raw) + 3) // 4 * 4
        return struct.pack("<III", 0 if last else size, action, len(raw)) + raw.ljust(size - 12, b"\0")
    data = record(1, "Artist\\Album") + record(1, "Artist\\Album\\01 Song.flac", last=True)
    check(watch.parse_notify(data) == [(1, "Artist\\Album"), (1, "Artist\\Album\\01 Song.flac")],
          "what ReadDirectoryChangesW says changed")
    check(watch.relevant("Artist\\Album\\01 Song.flac") and watch.relevant("Artist\\New Album"), "music, folders")
    check(not watch.relevant("Artist\\Album\\01 Song.lrc") and not watch.relevant("Artist\\Album\\cover.jpg"),
          "not lyrics or covers the library writes itself")
    check(not watch.relevant(".stfolder\\x.flac"), "not hidden folders")


def phones() -> None:
    print("phones over MTP")
    import asyncio
    import logging
    import shutil
    import tempfile
    import types
    import wave
    sys.modules.setdefault("decky", types.SimpleNamespace(logger=logging.getLogger("decky")))
    from mutagen import File
    from mutagen.id3 import TALB, TIT2, TPE1, TRCK
    from musicdeck import mtp
    from musicdeck.services import phone as phonemod

    wpd = r"\\?\usb#vid_18d1&pid_4ee1#2A101FDH2004XS#{6ac27878-a6fa-4155-ba85-f98f491d4f33}"
    check(mtp.stable_key(wpd) == "vid_18d1&pid_4ee1#2a101fdh2004xs", "a phone's id survives a different USB port")
    hiby = r"\\?\usb#vid_18d1&pid_4ee2&mi_00#6&7a8269&0&0000#{6ac27878-a6fa-4155-ba85-f98f491d4f33}"
    check(mtp.stable_key(hiby, "7F2326FC29F4AFA1951679ABCD4E60EC") == "vid_18d1&pid_4ee2#7f2326fc29f4afa1951679abcd4e60ec",
          "a composite device (a HiBy R4): its own serial, not the one Windows made up for its port")
    found = devices.windows_phones([{"id": wpd, "name": "Pixel 8", "maker": "Google"}], set())
    check(found[0]["name"] == "Google Pixel 8" and found[0]["kind"] == "mtp" and found[0]["wpd"] == wpd,
          "a phone, by its maker and model")
    check(devices.windows_phones([{"id": wpd, "name": "Pixel 8", "maker": "Google"}], {wpd}) == [],
          "ejected: gone until it's unplugged")

    def song(path: str, title: str, album: str, track: int) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with wave.open(path, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(44100)
            w.writeframes(b"\0\0\0\0" * 44100)
        audio = File(path)
        if audio.tags is None:
            audio.add_tags()
        for frame in (TIT2(encoding=3, text=title), TPE1(encoding=3, text="The Band"), TALB(encoding=3, text=album),
                      TRCK(encoding=3, text=str(track))):
            audio.tags.add(frame)
        audio.save()

    with tempfile.TemporaryDirectory() as root:
        device = os.path.join(root, "phone", "Internal shared storage")  # the phone itself
        song(os.path.join(device, "Music", "The Band", "First", "01.wav"), "Opener", "First", 1)
        with open(os.path.join(device, "Music", "The Band", "First", "cover.jpg"), "wb") as f:
            f.write(b"\xff\xd8\xff\xe0 a cover")
        os.makedirs(os.path.join(device, "DCIM"))
        with open(os.path.join(device, "DCIM", "photo.jpg"), "wb") as f:
            f.write(b"a photo")
        fetched: list[str] = []

        def listing(exe, dev, tops):
            entries = []
            for directory, _, names in os.walk(device):
                for name in names:
                    full = os.path.join(directory, name)
                    rel = os.path.relpath(full, device).replace("\\", "/")
                    if rel.split("/")[0] in tops:
                        entries.append({"type": "file", "storage": "Internal shared storage", "path": rel,
                                        "id": rel, "size": os.path.getsize(full), "mtime": int(os.path.getmtime(full))})
            return [{"id": "s1", "name": "Internal shared storage", "free": 5 * 10 ** 9, "total": 64 * 10 ** 9}], entries

        def fetch(exe, dev, wanted, done=None):
            for object_id, dest in wanted:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copyfile(os.path.join(device, *object_id.split("/")), dest)
                fetched.append(object_id)
            return {object_id for object_id, _ in wanted}

        def put(exe, dev, storage, folder, path, name=None):
            check(storage == "Internal shared storage", f"copied to its storage, into {folder}")
            dest = os.path.join(device, *folder.split("/"), name or os.path.basename(path))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(path, dest)
            return "new-object"
        mtp.listing, mtp.fetch, mtp.put = listing, fetch, put

        service = phonemod.PhoneService("dvphone", {"kind": "mtp", "wpd": wpd}, os.path.join(root, "data"))
        service.exe = "mtp-helper"
        service._scan_blocking()
        titles = sorted(t.get("title") for t in service.tracks.values())
        check(titles == ["Opener"], f"its songs indexed, by their tags ({titles})")
        cover = os.path.join(service.mirror, "Internal shared storage", "Music", "The Band", "First", "cover.jpg")
        check(os.path.isfile(cover), "a folder's cover is copied, so albums show it")
        check(not any("DCIM" in f for f in fetched), "photos aren't touched")
        check(service.space() == {"total": 64 * 10 ** 9, "free": 5 * 10 ** 9}, "its free space is the phone's")

        phonemod.CACHE_BYTES = 0
        service._scan_blocking()
        (tid, t), = service.tracks.items()
        check(not os.path.isfile(t["path"]) and t.get("title") == "Opener",
              "songs beyond the cache leave the PC, and stay indexed")
        before = len(fetched)
        service._scan_blocking()
        check(len(fetched) == before and len(service.tracks) == 1, "an unchanged phone isn't read again")
        source = asyncio.run(service.stream(tid))
        check(os.path.isfile(source.path) and len(fetched) == before + 1, "played: copied off the phone first")

        new = os.path.join(root, "Second.wav")
        song(new, "Closer", "Second", 2)
        where = os.path.join(service.mirror, "Music", "The Band", "Second")
        check(service.copy_in(new, where, "02 Closer.wav") == 1, "copied to the phone")
        check(os.path.isfile(os.path.join(device, "Music", "The Band", "Second", "02 Closer.wav")),
              "where its Music folder is")
        check(service.copy_in(new, where, "02 Closer.wav") == 0, "already there: left")
        phonemod.CACHE_BYTES = 2 * 1024 ** 3
        service._scan_blocking()
        check(sorted(t.get("title") for t in service.tracks.values()) == ["Closer", "Opener"], "and then it's indexed")

        os.remove(os.path.join(device, "Music", "The Band", "First", "01.wav"))
        service._scan_blocking()
        check([t.get("title") for t in service.tracks.values()] == ["Closer"], "deleted on the phone: gone here")


def fake_iphone(files: dict[str, bytes]) -> tuple[str, int]:
    """Apple Mobile Device Service and an iPhone behind it, as far as MusicDeck talks to them: usbmux on a
    port of its own, lockdownd and AFC through it (no TLS: the fake asks for none). `files` are its media
    share's, by path."""
    import plistlib
    import socket
    import threading
    from musicdeck import ios
    afc_port = 5000
    folders = {"/"} | {p[:i] for p in files for i in range(1, len(p)) if p[i] == "/"}
    record = {"HostID": "HOST-1", "SystemBUID": "BUID-1", "HostCertificate": b"", "HostPrivateKey": b""}

    def recv(conn, n):
        out = b""
        while len(out) < n:
            chunk = conn.recv(n - len(out))
            if not chunk:
                raise ConnectionError
            out += chunk
        return out

    def lockdown(conn):
        while True:
            length, = struct.unpack(">I", recv(conn, 4))
            req = plistlib.loads(recv(conn, length))
            reply = {"StartSession": {"SessionID": "S", "EnableSessionSSL": False},
                     "GetValue": {"Value": "Sam's iPhone" if req.get("Key") == "DeviceName" else None},
                     "StartService": {"Port": afc_port, "EnableServiceSSL": False}}.get(req["Request"], {"Error": "No"})
            body = plistlib.dumps(reply)
            conn.sendall(struct.pack(">I", len(body)) + body)

    def afc(conn):
        handles: dict[int, list] = {}
        while True:
            head = recv(conn, 40)
            total, this, number, op = struct.unpack("<QQQQ", head[8:])
            args = recv(conn, total - 40)
            path = args.split(b"\0")[0].decode()
            out_op, data = ios.AFC_DATA, b""

            def status(code):
                return ios.AFC_STATUS, struct.pack("<Q", code)
            if op == ios.AFC_READ_DIR:
                kids = sorted({p[len(path) + 1:].split("/")[0] for p in list(files) + list(folders)
                               if p.startswith(path + "/") and len(p) > len(path) + 1})
                out_op, data = (ios.AFC_DATA, b"\0".join(n.encode() for n in [".", ".."] + kids) + b"\0") \
                    if path in folders else status(8)
            elif op == ios.AFC_GET_FILE_INFO:
                if path in files:
                    data = f"st_size\0{len(files[path])}\0st_mtime\0{1700000000 * 10 ** 9}\0st_ifmt\0S_IFREG\0".encode()
                elif path in folders:
                    data = b"st_size\0" + b"0\0st_ifmt\0S_IFDIR\0"
                else:
                    out_op, data = status(8)
            elif op == ios.AFC_GET_DEVINFO:
                data = b"FSTotalBytes\x0064000000000\x00FSFreeBytes\x005000000000\x00"
            elif op == ios.AFC_FILE_OPEN:
                name = args[8:].split(b"\0")[0].decode()
                if name in files:
                    handles[len(handles) + 1] = [name, 0]
                    out_op, data = ios.AFC_FILE_OPEN_RES, struct.pack("<Q", len(handles))
                else:
                    out_op, data = status(8)
            elif op == ios.AFC_FILE_READ:
                handle, size = struct.unpack("<QQ", args[:16])
                name, at = handles[handle]
                data = files[name][at:at + size]
                handles[handle][1] += len(data)
            else:
                out_op, data = status(0)
            conn.sendall(ios.afc_packet(number, out_op, b"", data))

    def serve(conn):
        try:
            length, _, _, tag = struct.unpack("<IIII", recv(conn, 16))
            msg = plistlib.loads(recv(conn, length - 16))

            def reply(payload):
                body = plistlib.dumps(payload)
                conn.sendall(struct.pack("<IIII", 16 + len(body), 1, 8, tag) + body)
            kind = msg["MessageType"]
            if kind == "ListDevices":
                reply({"DeviceList": [{"DeviceID": 7, "MessageType": "Attached",
                                       "Properties": {"ConnectionType": "USB", "DeviceID": 7, "SerialNumber": "00008030-UDID"}}]})
            elif kind == "ReadPairRecord":
                reply({"PairRecordData": plistlib.dumps(record)} if msg["PairRecordID"] == "00008030-UDID" else {"Number": 2})
            elif kind == "Connect":
                port = struct.unpack(">H", struct.pack("<H", msg["PortNumber"]))[0]
                reply({"MessageType": "Result", "Number": 0})
                (lockdown if port == ios.LOCKDOWN_PORT else afc)(conn)
        except (ConnectionError, OSError):
            pass
        finally:
            conn.close()

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(16)

    def accept():
        while True:
            conn, _ = server.accept()
            threading.Thread(target=serve, args=(conn,), daemon=True).start()
    threading.Thread(target=accept, daemon=True).start()
    return server.getsockname()


def iphones() -> None:
    print("iPhones, through Apple's service")
    import asyncio
    import logging
    import tempfile
    import types
    import wave
    sys.modules.setdefault("decky", types.SimpleNamespace(logger=logging.getLogger("decky")))
    from mutagen import File
    from mutagen.id3 import TALB, TIT2, TPE1
    from musicdeck import ios
    from musicdeck.services import phone as phonemod
    from musicdeck.services.iphone import IphoneService

    with tempfile.TemporaryDirectory() as root:
        path = os.path.join(root, "song.wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(44100)
            w.writeframes(b"\0\0\0\0" * 44100)
        audio = File(path)
        audio.add_tags()
        for frame in (TIT2(encoding=3, text="Synced Song"), TPE1(encoding=3, text="The Band"),
                      TALB(encoding=3, text="On the Phone")):
            audio.tags.add(frame)
        audio.save()
        with open(path, "rb") as f:
            song = f.read()
        ios.USBMUX = fake_iphone({"/iTunes_Control/Music/F00/ABCD.wav": song,
                                  "/iTunes_Control/Music/F01/WXYZ.wav": song[:44] + b"x" * 20})
        check(ios.devices() == [{"udid": "00008030-UDID", "device_id": 7}], "the iPhone plugged in")
        check(ios.pair_record("00008030-UDID")["HostID"] == "HOST-1", "this PC's pair record, kept by Apple's service")
        check(ios.pair_record("someone-else") is None, "no pair record: not trusted")
        watcher = devices.DeviceWatcher(None, None)
        check(watcher._iphone_name({"udid": "00008030-UDID", "device_id": 7}) == "Sam's iPhone",
              "its name, through lockdownd")
        found = devices.windows_iphones(ios.devices(), {"00008030-UDID": "Sam's iPhone"}, set())
        linux = devices.phone_storage.__globals__["_device_id"]("afc:00008030-UDID")
        check(found[0]["id"] == linux and found[0]["kind"] == "ios", "the same id it has on Linux")
        check(devices.windows_iphones(ios.devices(), {}, set()) == [], "not trusted yet: not shown")

        afc, name = ios.Afc.open(7, ios.pair_record("00008030-UDID"))
        listed = sorted(f["path"] for f in afc.walk(ios.MUSIC))
        check(listed == ["F00/ABCD.wav", "F01/WXYZ.wav"], f"its synced music, by AFC ({listed})")
        check(afc.space() == {"total": 64000000000, "free": 5000000000}, "its free space")
        dest = os.path.join(root, "read.wav")
        afc.read("/iTunes_Control/Music/F00/ABCD.wav", dest)
        with open(dest, "rb") as f:
            check(f.read() == song, "a song read off it, whole")
        afc.close()

        service = IphoneService("dvios", {"kind": "ios", "udid": "00008030-UDID", "device_id": 7},
                                os.path.join(root, "data"))
        service._scan_blocking()
        titles = sorted(t.get("title") or "" for t in service.tracks.values())
        check("Synced Song" in titles, f"indexed by its tags ({titles})")
        check(not service.writable() and service.space()["free"] == 5000000000, "read only; the phone's free space")
        phonemod.CACHE_BYTES = 0
        service._scan_blocking()
        tid = next(t for t, v in service.tracks.items() if v.get("title") == "Synced Song")
        source = asyncio.run(service.stream(tid))
        with open(source.path, "rb") as f:
            check(f.read() == song, "played: read off the phone again")
        phonemod.CACHE_BYTES = 2 * 1024 ** 3


def outputs() -> None:
    print("the sound output, for its equalizer")
    check(winio.output_name("Speakers", "Realtek(R) Audio") == "Speakers", "the PC's speakers")
    check(winio.output_name("Headphones", "WH-1000XM4") == "WH-1000XM4", "Bluetooth headphones, by their name")
    check(winio.output_name("LG TV", "NVIDIA High Definition Audio") == "LG TV", "a TV over HDMI, by its name")
    if not winio.WINDOWS:
        return
    import asyncio
    import logging
    from musicdeck.audio_output import OutputWatcher

    async def watch():
        seen = []

        async def changed(output):
            seen.append(output)
        watcher = OutputWatcher(changed, logging.getLogger("test"))
        watcher.start()
        await asyncio.sleep(1)
        await watcher.stop()
        return seen
    seen = asyncio.run(watch())
    check(len(seen) == 1 and seen[0] and seen[0]["id"], f"this PC's output, followed ({seen})")


def this_pc() -> None:
    if not winio.WINDOWS:
        print("(not Windows: this PC's drives not asked)")
        return
    print("this PC")
    drives = devices._windows_drives()
    check(bool(drives), f"its drives are read ({''.join(d['letter'] for d in drives)})")
    found = devices.windows_storage(drives, devices.is_ipod)
    check(all(d["kind"] in ("usb", "ipod", "disc") for d in found), f"what's plugged in ({[d['name'] for d in found]})")
    system = os.environ.get("SystemDrive", "C:")[0]
    check(all(d["mount"][0] != system for d in found), "the Windows drive isn't a device")
    check(all(d.startswith("\\\\.\\") for d in cd.drives()), f"CD drives ({cd.drives()})")
    watcher = devices.DeviceWatcher(None, None)
    check(watcher.available, "devices are watched on Windows")


if __name__ == "__main__":
    parsing()
    plugged_in()
    recycle_bin()
    watching()
    phones()
    iphones()
    outputs()
    this_pc()
    print("all Windows checks passed")
