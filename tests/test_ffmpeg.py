import asyncio

import pytest

from babymonitorvl.ffmpeg import (
    FrameReadTimeout,
    build_ffmpeg_command,
    describe_rtsp_failure,
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


def test_ffmpeg_command_streams_continuous_frames_without_resizing_or_shell() -> None:
    command = build_ffmpeg_command("ffmpeg", "rtsp://camera/stream", "tcp", 12.5)
    assert command[0] == "ffmpeg"
    assert "-rtsp_transport" in command
    assert "-vf" not in command
    assert "-frames:v" not in command
    assert all("scale" not in argument for argument in command)
    assert command[command.index("-timeout") + 1] == "12500000"
    assert command.index("-timeout") < command.index("-i")
    assert "-rw_timeout" not in command
    assert command[-1] == "pipe:1"


@pytest.mark.parametrize(
    ("diagnostic", "expected"),
    [
        ("method DESCRIBE failed: 401 Unauthorized", "RTSP 认证失败"),
        ("Connection to tcp://camera:554 failed: Connection refused", "RTSP 连接被拒绝"),
        ("Connection timed out", "RTSP 连接超时"),
        ("Server returned 404 Not Found", "RTSP 码流不存在"),
        ("Failed to resolve hostname camera.invalid", "无法解析 RTSP 主机名"),
        (
            "FFmpeg produced no complete JPEG frame for 30 seconds",
            "30 秒内没有收到完整视频帧",
        ),
    ],
)
def test_rtsp_failures_have_clear_operator_messages(diagnostic: str, expected: str) -> None:
    message = describe_rtsp_failure([diagnostic], 30)
    assert expected in message
    assert diagnostic in message


def test_unknown_rtsp_failure_has_actionable_fallback_and_bounded_diagnostic() -> None:
    message = describe_rtsp_failure(["x" * 500], 30)
    assert "检查地址、凭据、网络和摄像头状态" in message
    assert message.endswith("...")
    assert len(message) < 350


@pytest.mark.asyncio
async def test_complete_frame_watchdog_fails_a_stalled_pipe() -> None:
    reader = asyncio.StreamReader()
    frames = read_mjpeg_frames(reader, frame_timeout_seconds=0.01)
    with pytest.raises(FrameReadTimeout, match="no complete JPEG frame"):
        await anext(frames)
