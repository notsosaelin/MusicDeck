"""
What every HTTPS connection MusicDeck makes shares: one TLS context, verifying against certifi's
certificate bundle (Decky's Python doesn't see the system's). Loading the bundle costs about 1 MB
per context, so every source, the stream server and the downloads use this one instead of their own.
"""
import functools
import ssl

import certifi


@functools.cache
def tls() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())
