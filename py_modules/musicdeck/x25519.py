"""
X25519 (RFC 7748): the Diffie-Hellman two MusicDeck installs pair with, in plain Python (Decky's Python has no
cryptography package, and pairing happens once, so its speed doesn't matter). Checked against the RFC's test vectors
in tests/test_backend.py.
"""
import secrets

P = 2 ** 255 - 19
A24 = 121665


def _clamp(scalar: bytes) -> int:
    k = bytearray(scalar)
    k[0] &= 248
    k[31] &= 127
    k[31] |= 64
    return int.from_bytes(k, "little")


def x25519(scalar: bytes, u: bytes) -> bytes:
    """The RFC's X25519 function: `scalar` times the point with u-coordinate `u` (both 32 bytes)."""
    if len(scalar) != 32 or len(u) != 32:
        raise ValueError("X25519 takes 32-byte values")
    k = _clamp(scalar)
    x1 = int.from_bytes(u, "little") & ((1 << 255) - 1)
    x2, z2, x3, z3, swap = 1, 0, x1, 1, 0
    for t in reversed(range(255)):
        bit = (k >> t) & 1
        swap ^= bit
        if swap:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = bit
        a, b = (x2 + z2) % P, (x2 - z2) % P
        aa, bb = a * a % P, b * b % P
        e = (aa - bb) % P
        c, d = (x3 + z3) % P, (x3 - z3) % P
        da, cb = d * a % P, c * b % P
        x3 = (da + cb) ** 2 % P
        z3 = x1 * (da - cb) ** 2 % P
        x2 = aa * bb % P
        z2 = e * (aa + A24 * e) % P
    if swap:
        x2, x3, z2, z3 = x3, x2, z3, z2
    return (x2 * pow(z2, P - 2, P) % P).to_bytes(32, "little")


BASE = (9).to_bytes(32, "little")


def keypair() -> tuple[bytes, bytes]:
    """A fresh (private, public) pair."""
    private = secrets.token_bytes(32)
    return private, x25519(private, BASE)


def shared(private: bytes, their_public: bytes) -> bytes:
    """The secret both sides arrive at; refuses the all-zero result a malicious public value gives."""
    out = x25519(private, their_public)
    if not any(out):
        raise ValueError("Unusable public key")
    return out
