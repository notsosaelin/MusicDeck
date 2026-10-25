"""
Loudness for songs without ReplayGain tags (foobar2000's ReplayGain scan), so volume levelling works for them too.

ffmpeg's EBU R128 meter (ebur128 with true peak) gives each song's integrated loudness and peak; ReplayGain 2.0's
gain is the distance to -18 LUFS. An album's gain comes from its songs together (their loudness averaged as energy,
weighted by length), so an album keeps its own dynamics. Written as REPLAYGAIN_* tags (tagging.write_replaygain),
where every player looks for them.
"""
import math
import re
import shutil
import subprocess
from typing import Optional

REFERENCE = -18.0  # LUFS: ReplayGain 2.0's target
INTEGRATED = re.compile(r"Integrated loudness:\s*\n\s*I:\s*(-?[\d.]+|-inf) LUFS")
PEAK = re.compile(r"True peak:\s*\n\s*Peak:\s*(-?[\d.]+|-inf) dBFS")
SILENT = -70.0  # what the meter says for silence: such a song gets no gain


def available() -> bool:
    return shutil.which("ffmpeg") is not None


def measure(path: str, timeout: float = 600) -> Optional[tuple[float, float]]:
    """(integrated loudness in LUFS, true peak as a linear 0..1+), or None when ffmpeg can't read it or it's silent."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    try:
        result = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-i", path, "-map", "0:a:0", "-af", "ebur128=peak=true",
                                 "-f", "null", "-"], capture_output=True, text=True, timeout=timeout,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return None
    loud, peak = INTEGRATED.search(result.stderr), PEAK.search(result.stderr)
    if not loud or loud.group(1) == "-inf" or float(loud.group(1)) <= SILENT:
        return None
    peak_db = float(peak.group(1)) if peak and peak.group(1) != "-inf" else -60.0
    return float(loud.group(1)), 10 ** (peak_db / 20)


def album(measured: list[tuple[float, float, float]]) -> tuple[float, float]:
    """An album's (loudness, peak) from its songs' (loudness, peak, seconds)."""
    weight = sum(max(d, 1.0) for _, _, d in measured)
    energy = sum(max(d, 1.0) * 10 ** (lufs / 10) for lufs, _, d in measured) / weight
    return 10 * math.log10(energy), max(p for _, p, _ in measured)


def gain(lufs: float) -> float:
    return round(REFERENCE - lufs, 2)
