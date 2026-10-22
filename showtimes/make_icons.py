"""Render the app icons from static/logo.svg (macOS: uses Quick Look and sips, both built in).

    python3 make_icons.py
"""
import subprocess
import tempfile
from pathlib import Path

STATIC = Path(__file__).parent / "static"


def main():
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["qlmanage", "-t", "-s", "1024", "-o", tmp, str(STATIC / "logo.svg")],
                       check=True, capture_output=True)
        big = Path(tmp) / "logo.svg.png"
        for size in (512, 192, 180, 32):
            out = STATIC / ("icon-%d.png" % size)
            subprocess.run(["sips", "-z", str(size), str(size), str(big), "--out", str(out)],
                           check=True, capture_output=True)
            print("wrote", out.name)


if __name__ == "__main__":
    main()
