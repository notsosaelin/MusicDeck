"""
Windows' side of what devices.py, cd.py and ipod.py read from Linux's /proc, sysfs and ioctls, through
the Win32 API (ctypes; nothing to install):

* Drives: the letters there are (GetLogicalDrives), what each is (GetDriveTypeW), its volume's label,
  serial number and file system (GetVolumeInformationW), and the hardware under it
  (IOCTL_STORAGE_QUERY_PROPERTY: the bus, the maker's and model's names, the serial number).
* The USB device a drive is on: the drive's device number (IOCTL_STORAGE_GET_DEVICE_NUMBER) matched with
  a disk or CD-ROM interface's (SetupAPI), then up the device tree (CM_Get_Parent) to the USB device,
  whose instance id is USB\\VID_xxxx&PID_yyyy\\<serial>. Ejecting it is CM_Request_Device_EjectW: what
  "Safely remove" does.
* SCSI commands to a drive (IOCTL_SCSI_PASS_THROUGH): an iPod's SysInfoExtended is in its INQUIRY vital
  product data pages (libgpod's ipod-scsi.c).
* CDs: the table of contents (IOCTL_CDROM_READ_TOC, in MSF), whether there's a disc
  (IOCTL_STORAGE_CHECK_VERIFY2), raw audio frames (IOCTL_CDROM_RAW_READ, CDDA), the tray
  (IOCTL_STORAGE_MEDIA_REMOVAL, IOCTL_STORAGE_EJECT_MEDIA).

Drives are named by letter ("E"); a device path is \\\\.\\E:. Everything here returns None, [] or False
rather than raising when Windows says no (a drive that went away, one that's busy).
"""
import contextlib
import ctypes
import os
import re
import struct
from ctypes import wintypes
from typing import Any, Iterator, Optional

WINDOWS = os.name == "nt"

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
OPEN_EXISTING = 3
INVALID_HANDLE = ctypes.c_void_p(-1).value

DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_CDROM = 5
FILE_READ_ONLY_VOLUME = 0x00080000

IOCTL_STORAGE_CHECK_VERIFY2 = 0x2D0800
IOCTL_STORAGE_MEDIA_REMOVAL = 0x2D4804
IOCTL_STORAGE_EJECT_MEDIA = 0x2D4808
IOCTL_STORAGE_GET_DEVICE_NUMBER = 0x2D1080
IOCTL_STORAGE_QUERY_PROPERTY = 0x2D1400
IOCTL_CDROM_READ_TOC = 0x24000
IOCTL_CDROM_RAW_READ = 0x2403E
IOCTL_SCSI_PASS_THROUGH = 0x4D004

BUS_USB = 7
BUS_1394 = 4
CDDA = 2  # TRACK_MODE_TYPE: raw audio
#: frames read at once: Windows' CD-ROM driver takes up to 64 KiB in one request
RAW_CHUNK = 27

GUID_DEVINTERFACE_DISK = "{53F56307-B6BF-11D0-94F2-00A0C91EFB8B}"
GUID_DEVINTERFACE_CDROM = "{53F56308-B6BF-11D0-94F2-00A0C91EFB8B}"
DIGCF_PRESENT = 0x2
DIGCF_DEVICEINTERFACE = 0x10

if WINDOWS:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
    cfgmgr32 = ctypes.WinDLL("cfgmgr32")
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.DeviceIoControl.restype = wintypes.BOOL
    kernel32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                         wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                         wintypes.LPVOID]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
                                               ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                                               ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD]
    kernel32.SetErrorMode.argtypes = [wintypes.UINT]
    setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
    setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
    setupapi.SetupDiEnumDeviceInterfaces.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                                     wintypes.DWORD, ctypes.c_void_p]
    setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                                          wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                                          ctypes.c_void_p]
    cfgmgr32.CM_Get_Parent.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, wintypes.ULONG]
    cfgmgr32.CM_Get_Device_IDW.argtypes = [wintypes.DWORD, wintypes.LPWSTR, wintypes.ULONG, wintypes.ULONG]
    cfgmgr32.CM_Request_Device_EjectW.argtypes = [wintypes.DWORD, ctypes.POINTER(ctypes.c_int), wintypes.LPWSTR,
                                                  wintypes.ULONG, wintypes.ULONG]


def device_path(letter: str) -> str:
    return f"\\\\.\\{letter[0].upper()}:"


@contextlib.contextmanager
def opened(path: str, access: int = GENERIC_READ) -> Iterator[Optional[int]]:
    """A handle on a device (None when it won't open), closed after."""
    handle = kernel32.CreateFileW(path, access, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
    if handle in (None, INVALID_HANDLE):
        yield None
        return
    try:
        yield handle
    finally:
        kernel32.CloseHandle(handle)


def ioctl(handle: int, code: int, data: bytes = b"", out_size: int = 0) -> Optional[bytes]:
    """DeviceIoControl: what came back (out_size at most), or None when it failed."""
    inbuf = ctypes.create_string_buffer(data, len(data)) if data else None
    outbuf = ctypes.create_string_buffer(out_size) if out_size else None
    got = wintypes.DWORD(0)
    ok = kernel32.DeviceIoControl(handle, code, inbuf, len(data), outbuf, out_size, ctypes.byref(got), None)
    if not ok:
        return None
    return outbuf.raw[:got.value] if outbuf is not None else b""


@contextlib.contextmanager
def quiet() -> Iterator[None]:
    """No "There is no disk in the drive" dialog box while an empty drive is asked about."""
    old = kernel32.SetErrorMode(0x8001)  # SEM_FAILCRITICALERRORS | SEM_NOOPENFILEERRORBOX
    try:
        yield
    finally:
        kernel32.SetErrorMode(old)


# --- drives -----------------------------------------------------------------------------------------

def letters() -> list[str]:
    mask = kernel32.GetLogicalDrives()
    return [chr(65 + i) for i in range(26) if mask >> i & 1]


def drive_type(letter: str) -> int:
    return kernel32.GetDriveTypeW(f"{letter}:\\")


def volume(letter: str) -> Optional[dict]:
    """{"label", "serial" ("1A2B-3C4D", as Linux names a FAT volume), "fs" ("FAT32", "exFAT", "NTFS",
    "CDFS", "UDF"), "read_only"}; None with no volume (an empty drive, an audio CD on some systems)."""
    label = ctypes.create_unicode_buffer(261)
    fs = ctypes.create_unicode_buffer(261)
    serial, length, flags = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
    with quiet():
        ok = kernel32.GetVolumeInformationW(f"{letter}:\\", label, 261, ctypes.byref(serial), ctypes.byref(length),
                                            ctypes.byref(flags), fs, 261)
    if not ok:
        return None
    return {"label": label.value, "serial": f"{serial.value >> 16:04X}-{serial.value & 0xFFFF:04X}",
            "fs": fs.value, "read_only": bool(flags.value & FILE_READ_ONLY_VOLUME)}


def parse_device_descriptor(data: bytes) -> dict:
    """STORAGE_DEVICE_DESCRIPTOR: {"bus", "removable", "vendor", "product", "serial"}."""
    if len(data) < 36:
        return {}
    removable = data[10]
    vendor_at, product_at, _, serial_at, bus = struct.unpack_from("<IIIII", data, 12)

    def text(at: int) -> str:
        if not at or at >= len(data):
            return ""
        return data[at:data.index(b"\0", at) if b"\0" in data[at:] else len(data)].decode("ascii", "replace").strip()
    return {"bus": bus, "removable": bool(removable), "vendor": text(vendor_at), "product": text(product_at),
            "serial": text(serial_at)}


def storage(letter_or_path: str) -> dict:
    """What a drive's hardware says it is (parse_device_descriptor); {} when it won't say."""
    path = letter_or_path if letter_or_path.startswith("\\\\") else device_path(letter_or_path)
    with opened(path, 0) as handle:
        if handle is None:
            return {}
        query = struct.pack("<III", 0, 0, 0)  # StorageDeviceProperty, PropertyStandardQuery
        data = ioctl(handle, IOCTL_STORAGE_QUERY_PROPERTY, query, 1024)
    return parse_device_descriptor(data or b"")


def device_number(path: str) -> Optional[tuple[int, int]]:
    """(device type, number) of a drive or disk: what ties a volume to the disk it's on."""
    with opened(path, 0) as handle:
        if handle is None:
            return None
        data = ioctl(handle, IOCTL_STORAGE_GET_DEVICE_NUMBER, b"", 12)
    if not data or len(data) < 8:
        return None
    kind, number, _ = struct.unpack("<IIi", data[:12].ljust(12, b"\0"))
    return kind, number


# --- the USB device a drive is on ------------------------------------------------------------------

class _Guid(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


class _InterfaceData(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", _Guid), ("Flags", wintypes.DWORD),
                ("Reserved", ctypes.c_void_p)]


class _DevinfoData(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("ClassGuid", _Guid), ("DevInst", wintypes.DWORD),
                ("Reserved", ctypes.c_void_p)]


def _guid(text: str) -> _Guid:
    parts = text.strip("{}").split("-")
    tail = bytes.fromhex(parts[3] + parts[4])
    return _Guid(int(parts[0], 16), int(parts[1], 16), int(parts[2], 16), (ctypes.c_ubyte * 8)(*tail))


def _interfaces(guid_text: str) -> Iterator[tuple[str, int]]:
    """(device path, device instance) of every present device with that interface."""
    guid = _guid(guid_text)
    devs = setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if devs in (None, INVALID_HANDLE):
        return
    try:
        index = 0
        while True:
            iface = _InterfaceData()
            iface.cbSize = ctypes.sizeof(_InterfaceData)
            if not setupapi.SetupDiEnumDeviceInterfaces(devs, None, ctypes.byref(guid), index, ctypes.byref(iface)):
                break
            index += 1
            needed = wintypes.DWORD()
            setupapi.SetupDiGetDeviceInterfaceDetailW(devs, ctypes.byref(iface), None, 0, ctypes.byref(needed), None)
            if not needed.value:
                continue
            detail = ctypes.create_string_buffer(needed.value)
            # SP_DEVICE_INTERFACE_DETAIL_DATA_W: cbSize (8 on 64-bit, 6 on 32-bit), then the path
            ctypes.c_uint32.from_buffer(detail).value = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            info = _DevinfoData()
            info.cbSize = ctypes.sizeof(_DevinfoData)
            if setupapi.SetupDiGetDeviceInterfaceDetailW(devs, ctypes.byref(iface), detail, needed.value,
                                                         None, ctypes.byref(info)):
                path = ctypes.wstring_at(ctypes.addressof(detail) + 4)
                yield path, info.DevInst
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(devs)


def _device_id(devinst: int) -> str:
    buf = ctypes.create_unicode_buffer(400)
    return buf.value if cfgmgr32.CM_Get_Device_IDW(devinst, buf, 400, 0) == 0 else ""


USB_ID = re.compile(r"^USB\\VID_([0-9A-F]{4})&PID_([0-9A-F]{4})(?:&[^\\]*)?\\(.+)$", re.IGNORECASE)


def parse_usb_id(instance_id: str) -> Optional[tuple[int, int, str]]:
    """(vendor id, product id, serial) from a USB device's instance id; Windows makes up a serial with
    "&" in it for a device that has none, which isn't one."""
    m = USB_ID.match(instance_id)
    if not m:
        return None
    serial = m.group(3)
    return int(m.group(1), 16), int(m.group(2), 16), "" if "&" in serial else serial


def usb_device(letter: str, cdrom: bool = False) -> Optional[tuple[int, str]]:
    """(device instance, USB instance id) of the USB device a drive is on; None when it isn't on USB."""
    wanted = device_number(device_path(letter))
    if not wanted:
        return None
    for path, devinst in _interfaces(GUID_DEVINTERFACE_CDROM if cdrom else GUID_DEVINTERFACE_DISK):
        if device_number(path) != wanted:
            continue
        node = wintypes.DWORD(devinst)
        for _ in range(8):  # disk -> USBSTOR -> USB device, a hub or two at most in between
            ident = _device_id(node.value)
            if USB_ID.match(ident):
                return node.value, ident
            parent = wintypes.DWORD()
            if cfgmgr32.CM_Get_Parent(ctypes.byref(parent), node.value, 0) != 0:
                break
            node = parent
        return None
    return None


VETOES = {1: "something has it open", 2: "Windows is using it", 3: "a driver won't let it go",
          4: "a program won't let it go", 5: "it's busy", 6: "a service is using it"}


def eject_usb(devinst: int) -> str:
    """Safely removes a USB device; "" when it's done, else why not."""
    veto = ctypes.c_int(0)
    name = ctypes.create_unicode_buffer(260)
    result = cfgmgr32.CM_Request_Device_EjectW(devinst, ctypes.byref(veto), name, 260, 0)
    if result == 0 and veto.value == 0:
        return ""
    if veto.value:
        return f"It's still in use ({VETOES.get(veto.value, 'Windows said no')}); try again in a moment"
    return "Windows wouldn't eject it"


# --- SCSI ---------------------------------------------------------------------------------------------

class _PassThrough(ctypes.Structure):
    _fields_ = [("Length", wintypes.USHORT), ("ScsiStatus", ctypes.c_ubyte), ("PathId", ctypes.c_ubyte),
                ("TargetId", ctypes.c_ubyte), ("Lun", ctypes.c_ubyte), ("CdbLength", ctypes.c_ubyte),
                ("SenseInfoLength", ctypes.c_ubyte), ("DataIn", ctypes.c_ubyte),
                ("DataTransferLength", wintypes.ULONG), ("TimeOutValue", wintypes.ULONG),
                ("DataBufferOffset", ctypes.c_size_t), ("SenseInfoOffset", wintypes.ULONG),
                ("Cdb", ctypes.c_ubyte * 16)]


def scsi_in(handle: int, cdb: bytes, length: int, timeout: int = 10) -> Optional[bytes]:
    """A SCSI command whose answer is read from the device (data in), through IOCTL_SCSI_PASS_THROUGH."""
    head = ctypes.sizeof(_PassThrough)
    sense = 32
    spt = _PassThrough()
    spt.Length = head
    spt.CdbLength = len(cdb)
    spt.SenseInfoLength = sense
    spt.DataIn = 1  # SCSI_IOCTL_DATA_IN
    spt.DataTransferLength = length
    spt.TimeOutValue = timeout
    spt.SenseInfoOffset = head
    spt.DataBufferOffset = head + sense
    for i, b in enumerate(cdb):
        spt.Cdb[i] = b
    request = bytes(spt) + bytes(sense + length)
    data = ioctl(handle, IOCTL_SCSI_PASS_THROUGH, request, len(request))
    if data is None or len(data) < head:
        return None
    back = _PassThrough.from_buffer_copy(data[:head])
    if back.ScsiStatus != 0:
        return None
    return data[head + sense:head + sense + back.DataTransferLength]


def inquiry_page(handle: int, page: int) -> Optional[bytes]:
    """An INQUIRY vital product data page (EVPD)."""
    return scsi_in(handle, bytes([0x12, 0x01, page, 0, 0xFF, 0]), 0xFF)


def sysinfo_extended_pages(read_page) -> str:
    """An iPod's SysInfoExtended from its vital product data, as libgpod's ipod-scsi.c reads it: page
    0xC0 lists the pages there are; pages from 0xC2 on each carry a piece of the XML after their 4-byte
    header (their length in byte 3)."""
    index = read_page(0xC0)
    if not index or len(index) < 4:
        return ""
    out = b""
    for page in index[4:4 + index[3]]:
        if page < 0xC2:
            continue
        data = read_page(page)
        if not data or len(data) < 4:
            break
        out += data[4:4 + data[3]]
    text = out.split(b"\0", 1)[0].decode("utf-8", "replace")
    return text if "<plist" in text else ""


def ipod_sysinfo_extended(letter: str) -> str:
    """SysInfoExtended from the iPod on a drive letter; "" when it won't say (or isn't one)."""
    with opened(device_path(letter), GENERIC_READ | GENERIC_WRITE) as handle:
        if handle is None:
            return ""
        return sysinfo_extended_pages(lambda page: inquiry_page(handle, page))


# --- CDs ----------------------------------------------------------------------------------------------

def parse_toc(data: bytes) -> list[tuple[int, int, bool]]:
    """CDROM_TOC: Length, FirstTrack, LastTrack, then 8-byte TRACK_DATA entries (Reserved, Control:4 |
    Adr:4, TrackNumber, Reserved1, Address[4] as MSF); the lead-out (0xAA) last. As cd.read_toc gives it:
    [(number, start LBA, audio?)], lead-out last."""
    if len(data) < 4:
        return []
    length, first, last = struct.unpack_from(">HBB", data)
    toc = []
    for at in range(4, min(len(data), 2 + length) - 7, 8):
        control, number, m, s, f = data[at + 1] & 0x0F, data[at + 2], data[at + 5], data[at + 6], data[at + 7]
        lba = (m * 60 + s) * 75 + f - 150
        toc.append((number, lba, number != 0xAA and not control & 0x04))
        if number == 0xAA:
            break
    return toc if toc and toc[-1][0] == 0xAA else []


def cd_toc(path: str) -> list[tuple[int, int, bool]]:
    with opened(path) as handle:
        if handle is None:
            raise OSError(f"can't open {path}")
        with quiet():
            data = ioctl(handle, IOCTL_CDROM_READ_TOC, b"", 804)
    if data is None:
        raise OSError(f"no table of contents on {path}")
    return parse_toc(data)


def cd_has_disc(path: str) -> bool:
    with opened(path, 0) as handle:
        if handle is None:
            return False
        with quiet():
            return ioctl(handle, IOCTL_STORAGE_CHECK_VERIFY2) is not None


def cd_read_audio(path: str, lba: int, count: int) -> bytes:
    """Raw CD audio frames (2352 bytes each), RAW_CHUNK at a time."""
    out = bytearray()
    with opened(path) as handle:
        if handle is None:
            raise OSError(f"can't open {path}")
        while count > 0:
            n = min(count, RAW_CHUNK)
            # RAW_READ_INFO: DiskOffset (the frame's place in 2048-byte sectors, as bytes), SectorCount, TrackMode
            data = ioctl(handle, IOCTL_CDROM_RAW_READ, struct.pack("<qII", lba * 2048, n, CDDA), n * 2352)
            if data is None:
                raise OSError(f"couldn't read the CD at {lba}")
            out += data
            lba += n
            count -= n
    return bytes(out)


def cd_eject(path: str) -> bool:
    with opened(path) as handle:
        if handle is None:
            return False
        ioctl(handle, IOCTL_STORAGE_MEDIA_REMOVAL, b"\0")  # PREVENT_MEDIA_REMOVAL: no
        return ioctl(handle, IOCTL_STORAGE_EJECT_MEDIA) is not None


# --- the sound output -----------------------------------------------------------------------------

CLSID_MM_DEVICE_ENUMERATOR = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
IID_IMM_DEVICE_ENUMERATOR = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
#: PKEY_Device_DeviceDesc ("Speakers", "Headphones", a TV's name), PKEY_DeviceInterface_FriendlyName
#: (the device: "Realtek(R) Audio", "WH-1000XM4")
PKEY_DEVICE_DESC = ("{A45C254E-DF1C-4EFD-8020-67D146A850E0}", 2)
PKEY_INTERFACE_NAME = ("{026E516E-B814-414B-83CD-856D6FEF4822}", 2)
E_RENDER, E_MULTIMEDIA = 0, 1
VT_LPWSTR = 31


class _PropertyKey(ctypes.Structure):
    _fields_ = [("fmtid", _Guid), ("pid", wintypes.DWORD)]


class _PropVariant(ctypes.Structure):
    _fields_ = [("vt", wintypes.USHORT), ("r1", wintypes.USHORT), ("r2", wintypes.USHORT), ("r3", wintypes.USHORT),
                ("value", ctypes.c_void_p), ("extra", ctypes.c_void_p)]


def _method(obj: ctypes.c_void_p, index: int, restype: Any, *argtypes: Any) -> Any:
    """A COM object's method by its place in the vtable (IUnknown's three first)."""
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])


def _release(obj: ctypes.c_void_p) -> None:
    if obj:
        _method(obj, 2, wintypes.ULONG)(obj)


def default_audio_output() -> Optional[dict]:
    """The output Windows plays on now, as audio_output.describe gives Linux's: {"id" (the endpoint's,
    the same for the same device), "name"}; None when Windows won't say (no output at all)."""
    ole32 = ctypes.WinDLL("ole32")
    ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED; already initialised is fine
    enumerator, device, store = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    try:
        if ole32.CoCreateInstance(ctypes.byref(_guid(CLSID_MM_DEVICE_ENUMERATOR)), None, 1,  # CLSCTX_INPROC_SERVER
                                  ctypes.byref(_guid(IID_IMM_DEVICE_ENUMERATOR)), ctypes.byref(enumerator)) != 0:
            return None
        get_default = _method(enumerator, 4, ctypes.HRESULT, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))
        try:
            get_default(enumerator, E_RENDER, E_MULTIMEDIA, ctypes.byref(device))
        except OSError:
            return None
        endpoint = ctypes.c_wchar_p()
        _method(device, 5, ctypes.HRESULT, ctypes.POINTER(ctypes.c_wchar_p))(device, ctypes.byref(endpoint))
        ident = endpoint.value or ""
        ole32.CoTaskMemFree(endpoint)
        _method(device, 4, ctypes.HRESULT, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p))(device, 0, ctypes.byref(store))
        get_value = _method(store, 5, ctypes.HRESULT, ctypes.POINTER(_PropertyKey), ctypes.POINTER(_PropVariant))

        def text(key: tuple[str, int]) -> str:
            value = _PropVariant()
            try:
                get_value(store, ctypes.byref(_PropertyKey(_guid(key[0]), key[1])), ctypes.byref(value))
            except OSError:
                return ""
            out = ctypes.wstring_at(value.value) if value.vt == VT_LPWSTR and value.value else ""
            ole32.PropVariantClear(ctypes.byref(value))
            return out
        return {"id": ident, "name": output_name(text(PKEY_DEVICE_DESC), text(PKEY_INTERFACE_NAME))} if ident else None
    finally:
        for obj in (store, device, enumerator):
            _release(obj)


def output_name(endpoint: str, device: str) -> str:
    """An output as people call it: the endpoint's own name ("Speakers", "Headphones", a TV over HDMI),
    or for a generic one on a Bluetooth or USB device ("Headphones" on "WH-1000XM4"), the device's."""
    generic = endpoint.lower() in ("speakers", "headphones", "headset", "headset earphone", "speaker", "")
    if generic and device and not re.search(r"realtek|high definition audio|intel|amd|nvidia|conexant", device, re.I):
        return device
    return endpoint or device or "Speakers"
