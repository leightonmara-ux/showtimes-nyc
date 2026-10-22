"""Generate the app icons (a red ticket stub on a dark ground) with no dependencies."""
import struct, zlib
from pathlib import Path

def png(size, path):
    bg, red, hole = (20, 17, 15), (194, 54, 31), (20, 17, 15)
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            u, v = x / size, y / size
            c = bg
            if 0.2 < u < 0.8 and 0.3 < v < 0.7:
                c = red
                # notches on the stub's short sides, a perforation line
                if ((u - 0.2) ** 2 + (v - 0.5) ** 2) < 0.006 or ((u - 0.8) ** 2 + (v - 0.5) ** 2) < 0.006:
                    c = hole
                if abs(u - 0.62) < 0.008 and int(v * 40) % 2 == 0:
                    c = hole
            row += bytes(c)
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 9)
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
                          + chunk(b"IDAT", raw) + chunk(b"IEND", b""))

for s in (180, 512):
    png(s, Path(__file__).parent / "static" / ("icon-%d.png" % s))
