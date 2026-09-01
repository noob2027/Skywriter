"""Generate the checked-in provisional ICO from simple repository-owned geometry."""

from __future__ import annotations

import argparse
import struct
import zlib
from pathlib import Path

RGBA = tuple[int, int, int, int]


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _pixel(size: int, x: int, y: int) -> RGBA:
    scale = size / 256.0
    left, top, right, bottom, radius = (int(value * scale) for value in (8, 8, 248, 248, 48))
    rounded = left <= x < right and top <= y < bottom
    if rounded:
        corner_x = left + radius if x < left + radius else right - radius - 1
        corner_y = top + radius if y < top + radius else bottom - radius - 1
        in_corner = (x < left + radius or x >= right - radius) and (
            y < top + radius or y >= bottom - radius
        )
        if in_corner and (x - corner_x) ** 2 + (y - corner_y) ** 2 > radius**2:
            rounded = False
    if not rounded:
        return (0, 0, 0, 0)

    background = (23, 63, 61, 255)
    white = (247, 251, 250, 255)
    accent = (141, 217, 212, 255)
    stroke = max(1, round(14 * scale))
    line_stroke = max(1, round(12 * scale))
    doc_left, doc_top, doc_right, doc_bottom = (
        round(value * scale) for value in (72, 52, 192, 204)
    )
    fold_x, fold_y = round(153 * scale), round(91 * scale)
    on_outline = (
        doc_left <= x <= doc_right
        and doc_top <= y <= doc_bottom
        and (
            x - doc_left < stroke
            or doc_right - x < stroke
            or y - doc_top < stroke
            or doc_bottom - y < stroke
        )
    )
    on_fold = (fold_x - stroke // 2 <= x <= fold_x + stroke // 2 and doc_top <= y <= fold_y) or (
        fold_x <= x <= doc_right and fold_y - stroke // 2 <= y <= fold_y + stroke // 2
    )
    on_line = any(
        round(line_y * scale) - line_stroke // 2 <= y <= round(line_y * scale) + line_stroke // 2
        and round(101 * scale) <= x <= round(163 * scale)
        for line_y in (127, 158)
    )
    if on_line:
        return accent
    if on_outline or on_fold:
        return white
    return background


def _png(size: int) -> bytes:
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            row.extend(_pixel(size, x, y))
        rows.append(bytes(row))
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
        + _chunk(b"IEND", b"")
    )


def create_icon(destination: Path) -> None:
    sizes = (16, 32, 48, 256)
    images = [_png(size) for size in sizes]
    offset = 6 + 16 * len(images)
    entries = []
    for size, image in zip(sizes, images, strict=True):
        width = 0 if size == 256 else size
        height = 0 if size == 256 else size
        entries.append(struct.pack("<BBBBHHII", width, height, 0, 0, 1, 32, len(image), offset))
        offset += len(image)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(struct.pack("<HHH", 0, 1, len(images)) + b"".join(entries + images))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    create_icon(arguments.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
