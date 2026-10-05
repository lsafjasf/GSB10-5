"""Optional ffmpeg-based frame source.

The library itself needs nothing beyond the standard library; decoding real
video files is delegated to an external ``ffmpeg`` binary via a pipe.  If
ffmpeg is not installed this module simply raises a clear error -- every
self-test works without it.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Iterator, Optional


def decode_gray(path: str, width: int = 128, height: int = 72,
                fps: Optional[float] = None) -> Iterator[bytes]:
    """Yield downscaled grayscale frames of a video file, one bytes per frame."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "ffmpeg not found; install ffmpeg or feed frames directly "
            "(see keyframe.synth for synthetic sources)"
        )
    vf = f"scale={width}:{height}"
    if fps:
        vf = f"fps={fps}," + vf
    cmd = [
        "ffmpeg", "-v", "error", "-i", path,
        "-vf", vf, "-pix_fmt", "gray",
        "-f", "rawvideo", "-",
    ]
    frame_size = width * height
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    assert proc.stdout is not None
    try:
        while True:
            buf = proc.stdout.read(frame_size)
            if len(buf) < frame_size:
                break
            yield buf
    finally:
        proc.stdout.close()
        proc.wait()
