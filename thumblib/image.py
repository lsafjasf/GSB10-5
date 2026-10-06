"""Planar 8-bit image container used across thumblib."""

MODES = {"L": 1, "LA": 2, "RGB": 3, "RGBA": 4}


class Img:
    """Planar 8-bit image. channels is a list of bytearray, each len == w*h."""

    __slots__ = ("width", "height", "mode", "channels")

    def __init__(self, width, height, mode, channels):
        if mode not in MODES:
            raise ValueError("bad mode %r" % (mode,))
        if width < 1 or height < 1:
            raise ValueError("bad geometry %dx%d" % (width, height))
        if len(channels) != MODES[mode]:
            raise ValueError("channel count mismatch")
        for ch in channels:
            if len(ch) != width * height:
                raise ValueError("channel size mismatch")
        self.width = width
        self.height = height
        self.mode = mode
        self.channels = list(channels)

    @property
    def nch(self):
        return MODES[self.mode]

    @classmethod
    def new(cls, width, height, mode, value=0):
        return cls(width, height, mode,
                   [bytearray([value]) * (width * height)
                    for _ in range(MODES[mode])])

    def copy(self):
        return Img(self.width, self.height, self.mode,
                   [bytearray(ch) for ch in self.channels])

    def float_planes(self):
        return [[float(v) for v in ch] for ch in self.channels]

    @classmethod
    def from_float_planes(cls, width, height, mode, planes):
        out = []
        for plane in planes:
            buf = bytearray(width * height)
            for i, v in enumerate(plane):
                if v <= 0.0:
                    buf[i] = 0
                elif v >= 255.0:
                    buf[i] = 255
                else:
                    buf[i] = int(v + 0.5)
            out.append(buf)
        return cls(width, height, mode, out)

    def luma_plane(self):
        """Return float luma list (Rec.601) of len w*h."""
        w, h = self.width, self.height
        if self.mode in ("L", "LA"):
            return [float(v) for v in self.channels[0]]
        r, g, b = self.channels[0], self.channels[1], self.channels[2]
        return [0.299 * r[i] + 0.587 * g[i] + 0.114 * b[i]
                for i in range(w * h)]
