#!/usr/bin/env python3
import struct
from pathlib import Path


ROOT = Path("/Users/matt/walkingpad")
ICONSET = ROOT / "macos" / "icon.iconset"
OUT = ROOT / "macos" / "WalkingPad.app" / "Contents" / "Resources" / "WalkingPad.icns"

CHUNKS = [
    ("icp4", "icon_16x16.png"),
    ("icp5", "icon_32x32.png"),
    ("icp6", "icon_32x32@2x.png"),
    ("ic07", "icon_128x128.png"),
    ("ic08", "icon_256x256.png"),
    ("ic09", "icon_512x512.png"),
    ("ic10", "icon_512x512@2x.png"),
    ("ic11", "icon_16x16@2x.png"),
    ("ic12", "icon_32x32@2x.png"),
    ("ic13", "icon_128x128@2x.png"),
    ("ic14", "icon_256x256@2x.png"),
]


def main():
    chunks = []
    for chunk_type, filename in CHUNKS:
        data = (ICONSET / filename).read_bytes()
        chunks.append(chunk_type.encode("ascii") + struct.pack(">I", len(data) + 8) + data)

    body = b"".join(chunks)
    OUT.write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)
    print(OUT)


if __name__ == "__main__":
    main()
