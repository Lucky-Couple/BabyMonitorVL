import asyncio

import pytest

from babymonitorvl.ffmpeg import (
    FrameReadTimeout,
    build_ffmpeg_command,
    jpeg_dimensions,
    read_mjpeg_frames,
)


def fake_jpeg(width: int = 32, height: int = 16) -> bytes:
    return (
        b"\xff\xd8\xff\xc0\x00\x0b\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x01\x01\x11\x00\xff\xd9"
    )


def test_jpeg_dimensions_without_cv_dependency() -> None:
    assert jpeg_dimensions(fake_jpeg(640, 360)) == (640, 360)


@pytest.mark.asyncio
async def test_reads_multiple_mjpeg_frames_across_chunks() -> None:
    first = fake_jpeg(32, 16)
    second = fake_jpeg(64, 48)
    reader = asyncio.StreamReader()
    payload = b"noise" + first + second
    reader.feed_data(payload[:13])
    reader.feed_data(payload[13:])
    reader.feed_eof()
    frames = [frame async for frame in read_mjpeg_frames(reader)]
    assert frames == [first, second]


def test_ffmpeg_command_captures_one_frame_without_resizing_or_shell() -> None:
    command = build_ffmpeg_command("ffmpeg", "rtsp://camera/stream", "tcp", 12.5)
    assert command[0] == "ffmpeg"
    assert "-rtsp_transport" in command
    assert "-vf" not in command
    assert command[command.index("-frames:v") + 1] == "1"
    assert all("scale" not in argument for argument in command)
    assert command[command.index("-timeout") + 1] == "12500000"
    assert command.index("-timeout") < command.index("-i")
    assert "-rw_timeout" not in command
    assert command[-1] == "pipe:1"


@pytest.mark.asyncio
async def test_complete_frame_watchdog_fails_a_stalled_pipe() -> None:
    reader = asyncio.StreamReader()
    frames = read_mjpeg_frames(reader, frame_timeout_seconds=0.01)
    with pytest.raises(FrameReadTimeout, match="no complete JPEG frame"):
        await anext(frames)
