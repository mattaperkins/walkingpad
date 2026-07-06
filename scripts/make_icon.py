#!/usr/bin/env python3
import math
import os
import struct
import zlib
from pathlib import Path


SIZE = 1024
ROOT = Path("/Users/matt/walkingpad")
OUT = ROOT / "macos" / "walkingpad_icon_1024.png"


def blend(dst, src):
    sr, sg, sb, sa = src
    if sa == 255:
        return src
    dr, dg, db, da = dst
    alpha = sa / 255.0
    inv = 1.0 - alpha
    return (
        int(sr * alpha + dr * inv),
        int(sg * alpha + dg * inv),
        int(sb * alpha + db * inv),
        255,
    )


def write_png(path, pixels, width, height):
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw.extend(pixels[y * width + x])

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)))
        fh.write(chunk(b"IDAT", zlib.compress(bytes(raw), 9)))
        fh.write(chunk(b"IEND", b""))


def in_rounded_rect(x, y, left, top, right, bottom, radius):
    if x < left or x > right or y < top or y > bottom:
        return False
    cx = min(max(x, left + radius), right - radius)
    cy = min(max(y, top + radius), bottom - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2


def dist_to_segment(px, py, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    length_sq = vx * vx + vy * vy
    if length_sq == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0, min(1, (wx * vx + wy * vy) / length_sq))
    cx, cy = ax + t * vx, ay + t * vy
    return math.hypot(px - cx, py - cy)


def render():
    pixels = [(0, 0, 0, 0)] * (SIZE * SIZE)
    center = (SIZE - 1) / 2

    for y in range(SIZE):
        for x in range(SIZE):
            nx = x / SIZE
            ny = y / SIZE
            color = None

            if in_rounded_rect(x, y, 64, 64, 960, 960, 210):
                # Blue-green panel with subtle depth.
                r = int(15 + 16 * nx)
                g = int(118 + 44 * (1 - ny))
                b = int(154 + 36 * ny)
                color = (r, g, b, 255)

                # Top-left highlight.
                glow = max(0, 1 - math.hypot(x - 245, y - 170) / 520)
                if glow:
                    color = blend(color, (78, 220, 205, int(74 * glow)))

                # Bottom shadow.
                shade = max(0, (ny - 0.58) / 0.42)
                if shade:
                    color = blend(color, (0, 29, 45, int(94 * shade)))

            if color:
                # Treadmill deck.
                if in_rounded_rect(x, y, 215, 570, 810, 715, 74):
                    color = blend(color, (236, 250, 249, 255))
                if in_rounded_rect(x, y, 268, 604, 755, 678, 36):
                    color = blend(color, (22, 52, 66, 255))
                if in_rounded_rect(x, y, 302, 623, 722, 660, 19):
                    color = blend(color, (39, 170, 154, 220))

                # Front rollers.
                if math.hypot(x - 245, y - 643) <= 46:
                    color = blend(color, (20, 48, 60, 255))
                if math.hypot(x - 778, y - 643) <= 46:
                    color = blend(color, (20, 48, 60, 255))
                if math.hypot(x - 245, y - 643) <= 20 or math.hypot(x - 778, y - 643) <= 20:
                    color = blend(color, (236, 250, 249, 255))

                # Handle/console stem.
                if dist_to_segment(x, y, 670, 555, 735, 300) <= 20:
                    color = blend(color, (236, 250, 249, 255))
                if in_rounded_rect(x, y, 654, 255, 825, 325, 34):
                    color = blend(color, (236, 250, 249, 255))
                if in_rounded_rect(x, y, 694, 278, 785, 303, 12):
                    color = blend(color, (17, 126, 154, 255))

                # Walking figure.
                if math.hypot(x - 438, y - 282) <= 48:
                    color = blend(color, (255, 255, 255, 255))
                for line in (
                    (438, 335, 390, 465, 24),
                    (405, 386, 308, 452, 18),
                    (410, 455, 334, 555, 22),
                    (405, 455, 515, 558, 22),
                    (429, 360, 536, 405, 18),
                ):
                    if dist_to_segment(x, y, *line[:4]) <= line[4]:
                        color = blend(color, (255, 255, 255, 255))

                # Outer edge treatment.
                edge = abs(math.hypot(x - center, y - center) - 590)
                if edge < 1:
                    color = blend(color, (255, 255, 255, 25))

            pixels[y * SIZE + x] = color or (0, 0, 0, 0)

    write_png(OUT, pixels, SIZE, SIZE)


if __name__ == "__main__":
    os.makedirs(OUT.parent, exist_ok=True)
    render()
    print(OUT)
