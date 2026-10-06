"""Minimal in-memory RGB image type (stdlib only)."""


class Image:
    """8-bit interleaved RGB image.

    data: bytearray of length width*height*3, row-major, R,G,B interleaved.
    """

    __slots__ = ("width", "height", "data")

    def __init__(self, width, height, data=None):
        if width < 1 or height < 1:
            raise ValueError("image dimensions must be >= 1")
        self.width = int(width)
        self.height = int(height)
        if data is None:
            self.data = bytearray(self.width * self.height * 3)
        else:
            if len(data) != self.width * self.height * 3:
                raise ValueError("data length does not match dimensions")
            self.data = bytearray(data)

    def rows(self):
        """Return the scanlines as a list of bytes objects."""
        stride = self.width * 3
        return [bytes(self.data[y * stride:(y + 1) * stride])
                for y in range(self.height)]

    @classmethod
    def from_rows(cls, width, height, rows):
        data = bytearray().join(bytes(r) for r in rows)
        return cls(width, height, data)

    def copy(self):
        return Image(self.width, self.height, self.data)
