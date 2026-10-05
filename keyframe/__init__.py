"""Pure-stdlib video keyframe extraction.

Pipeline: decode/downscale (optional ffmpeg) -> ShotDetector -> keyframes.
"""

from .detector import (DetectorConfig, Event, Shot, ShotDetector, detect,
                       iter_keyframes)
from .synth import SyntheticVideo
from .metrics import match_events, keyframe_density, MatchReport

__all__ = [
    "DetectorConfig", "Event", "Shot", "ShotDetector", "detect",
    "iter_keyframes", "SyntheticVideo", "match_events", "keyframe_density",
    "MatchReport",
]
