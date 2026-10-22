"""
yt-dlp worker, run as a separate process on the system's Python.

Decky's backend runs in a frozen interpreter that leaves out parts of the standard library; yt-dlp
imports some of them (e.g. getpass), and the fallback can load an incompatible copy from the
system's Python. Running yt-dlp here, on a complete interpreter, sidesteps that and keeps a slow
or stuck extraction from affecting the plugin.

Protocol: one JSON object per line on stdin -> one per line on stdout, matched by "id".
  {"id", "op": "extract", "video_id", "js_path"?, "cachedir", "format"}  -> {"id", "result": {...}}
  {"id", "op": "search", "query", "count"}                                -> {"id", "result": [...]}
  failures                                                                 -> {"id", "error": "..."}
"""
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

write_lock = threading.Lock()


def reply(message: dict) -> None:
    with write_lock:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()


def extract(req: dict) -> dict:
    import yt_dlp
    runtimes = {"quickjs": {"path": req["js_path"]}} if req.get("js_path") else {}
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
            "format": req["format"], "js_runtimes": runtimes, "cachedir": req["cachedir"]}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={req['video_id']}", download=False)
    return {"url": info.get("url"), "http_headers": info.get("http_headers") or {},
            "format_id": info.get("format_id"), "acodec": info.get("acodec"), "abr": info.get("abr")}


def search(req: dict) -> list:
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist", "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{int(req['count'])}:{req['query']}", download=False)
    keep = ("id", "title", "channel", "uploader", "channel_id", "duration", "thumbnails", "live_status", "ie_key")
    return [{k: e.get(k) for k in keep} for e in info.get("entries") or []]


def channel(req: dict) -> dict:
    """A YouTube channel's latest uploads (for uploaders that aren't YouTube Music artists)."""
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist", "skip_download": True,
            "playlistend": int(req.get("count", 60))}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/channel/{req['channel_id']}/videos", download=False)
    keep = ("id", "title", "channel", "uploader", "channel_id", "duration", "thumbnails", "live_status", "ie_key")
    return {"title": info.get("channel") or info.get("title") or "", "thumbnails": info.get("thumbnails") or [],
            "entries": [{k: e.get(k) for k in keep} for e in info.get("entries") or []]}


OPS = {"extract": extract, "search": search, "channel": channel}


def handle(req: dict) -> None:
    try:
        reply({"id": req["id"], "result": OPS[req["op"]](req)})
    except Exception as e:
        reply({"id": req.get("id"), "error": f"{type(e).__name__}: {e}"})


def main() -> None:
    # import once up front so the first real request doesn't pay for it
    import yt_dlp  # noqa: F401
    reply({"id": None, "ready": True, "version": yt_dlp.version.__version__})
    pool = ThreadPoolExecutor(max_workers=3)
    for line in sys.stdin:
        line = line.strip()
        if line:
            pool.submit(handle, json.loads(line))


if __name__ == "__main__":
    main()
