from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator


JPEG_START = b"\xff\xd8"
JPEG_END = b"\xff\xd9"


def build_ffmpeg_command(
    binary: str,
    rtsp_url: str,
    fps: float,
    transport: str,
    max_image_edge: int,
) -> list[str]:
    scale = (
        f"scale=w='if(gt(iw,ih),min(iw,{max_image_edge}),-2)'"
        f":h='if(gt(iw,ih),-2,min(ih,{max_image_edge}))'"
    )
    return [
        binary,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-rtsp_transport",
        transport,
        "-i",
        rtsp_url,
        "-an",
        "-vf",
        f"fps={fps},{scale}",
        "-q:v",
        "4",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "pipe:1",
    ]


async def read_mjpeg_frames(reader: asyncio.StreamReader) -> AsyncIterator[bytes]:
    buffer = bytearray()
    while True:
        chunk = await reader.read(64 * 1024)
        if not chunk:
            break
        buffer.extend(chunk)
        while True:
            start = buffer.find(JPEG_START)
            if start < 0:
                if len(buffer) > 1:
                    del buffer[:-1]
                break
            end = buffer.find(JPEG_END, start + 2)
            if end < 0:
                if start:
                    del buffer[:start]
                if len(buffer) > 32 * 1024 * 1024:
                    buffer.clear()
                break
            end += 2
            frame = bytes(buffer[start:end])
            del buffer[:end]
            yield frame


def jpeg_dimensions(data: bytes) -> tuple[int, int]:
    if not data.startswith(JPEG_START):
        raise ValueError("not a JPEG image")
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        index += 2
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        if index + 2 > len(data):
            break
        length = int.from_bytes(data[index : index + 2], "big")
        if length < 2 or index + length > len(data):
            break
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            return width, height
        index += length
    raise ValueError("JPEG dimensions not found")


async def collect_stderr(reader: asyncio.StreamReader, lines: list[str], limit: int = 20) -> None:
    while True:
        raw = await reader.readline()
        if not raw:
            return
        lines.append(raw.decode("utf-8", errors="replace").strip())
        del lines[:-limit]
