"""
AES-128 (FIPS-197), block by block and in CBC mode, for the iPod nano 5th generation's database
signature (hash72, itunesdb.py), which is AES-CBC with a key every iPod shares. Decky's Python has no
cryptography package and this is one or two blocks per sync, so plain Python does. Checked against
FIPS-197's own example (tests/test_ipod.py).
"""

SBOX = bytes.fromhex(
    "637c777bf26b6fc53001672bfed7ab76ca82c97dfa5947f0add4a2af9ca472c0b7fd9326363ff7cc34a5e5f171d8311504c723c31896059a071280e2eb27b275"
    "09832c1a1b6e5aa0523bd6b329e32f8453d100ed20fcb15b6acbbe394a4c58cfd0efaafb434d338545f9027f503c9fa851a3408f929d38f5bcb6da2110fff3d2"
    "cd0c13ec5f974417c4a77e3d645d197360814fdc222a908846eeb814de5e0bdbe0323a0a4906245cc2d3ac629195e479e7c8376d8dd54ea96c56f4ea657aae08"
    "ba78252e1ca6b4c6e8dd741f4bbd8b8a703eb5664803f60e613557b986c11d9ee1f8981169d98e949b1e87e9ce5528df8ca1890dbfe6426841992d0fb054bb16")
INV_SBOX = bytes(SBOX.index(i) for i in range(256))
RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)


def _xtime(a: int) -> int:
    a <<= 1
    return (a ^ 0x11B) if a & 0x100 else a


def _mul(a: int, b: int) -> int:
    out = 0
    while b:
        if b & 1:
            out ^= a
        a = _xtime(a)
        b >>= 1
    return out


def expand_key(key: bytes) -> list[bytes]:
    """The 11 round keys of an AES-128 key."""
    if len(key) != 16:
        raise ValueError("AES-128 takes a 16-byte key")
    words = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    for i in range(4, 44):
        w = list(words[i - 1])
        if i % 4 == 0:
            w = w[1:] + w[:1]
            w = [SBOX[b] for b in w]
            w[0] ^= RCON[i // 4 - 1]
        words.append([a ^ b for a, b in zip(words[i - 4], w)])
    return [bytes(sum(words[r * 4:r * 4 + 4], [])) for r in range(11)]


def _add(state: list[int], key: bytes) -> list[int]:
    return [s ^ k for s, k in zip(state, key)]


def _shift(state: list[int], inverse: bool = False) -> list[int]:
    # the state is column by column: byte (row r, column c) at 4c + r
    return [state[4 * ((c + (-r if inverse else r)) % 4) + r] for c in range(4) for r in range(4)]


def _mix(state: list[int], inverse: bool = False) -> list[int]:
    m = (14, 11, 13, 9) if inverse else (2, 3, 1, 1)
    out = []
    for c in range(4):
        col = state[4 * c:4 * c + 4]
        for r in range(4):
            out.append(_mul(col[0], m[(0 - r) % 4]) ^ _mul(col[1], m[(1 - r) % 4])
                       ^ _mul(col[2], m[(2 - r) % 4]) ^ _mul(col[3], m[(3 - r) % 4]))
    return out


def encrypt_block(keys: list[bytes], block: bytes) -> bytes:
    state = _add(list(block), keys[0])
    for r in range(1, 11):
        state = _shift([SBOX[b] for b in state])
        if r < 10:
            state = _mix(state)
        state = _add(state, keys[r])
    return bytes(state)


def decrypt_block(keys: list[bytes], block: bytes) -> bytes:
    state = _add(list(block), keys[10])
    for r in range(9, -1, -1):
        state = [INV_SBOX[b] for b in _shift(state, inverse=True)]
        state = _add(state, keys[r])
        if r > 0:
            state = _mix(state, inverse=True)
    return bytes(state)


def cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    keys, previous, out = expand_key(key), iv, b""
    for i in range(0, len(data), 16):
        previous = encrypt_block(keys, bytes(a ^ b for a, b in zip(data[i:i + 16], previous)))
        out += previous
    return out


def cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    keys, previous, out = expand_key(key), iv, b""
    for i in range(0, len(data), 16):
        block = data[i:i + 16]
        out += bytes(a ^ b for a, b in zip(decrypt_block(keys, block), previous))
        previous = block
    return out
