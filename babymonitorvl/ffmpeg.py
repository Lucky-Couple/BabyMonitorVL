from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator


JPEG_START = b"\xff\xd8"
JPEG_END = b"\xff\xd9"


class FrameReadTimeout(TimeoutError):
    """FFmpeg produced no complete JPEG frame within the watchdog interval."""


def describe_rtsp_failure(lines: list[str], timeout_seconds: float) -> str:
    """Translate common FFmpeg/RTSP failures while retaining bounded diagnostics."""

    useful = [line.strip() for line in lines if line.strip()]
    diagnostic = useful[-1] if useful else ""
    combined = " | ".join(useful).lower()
    if any(marker in combined for marker in ("401 unauthorized", "server returned 401", "failed: 401")):
        summary = "RTSP 认证失败：请检查摄像头用户名和密码。"
    elif any(marker in combined for marker in ("403 forbidden", "server returned 403", "failed: 403")):
        summary = "RTSP 访问被拒绝（403）：账号可能没有该码流的访问权限。"
    elif any(marker in combined for marker in ("404 not found", "server returned 404", "failed: 404")):
        summary = "RTSP 码流不存在（404）：请检查地址中的路径或 stream 名称。"
    elif "connection refused" in combined:
        summary = "RTSP 连接被拒绝：请确认摄像头地址、端口以及 RTSP 服务是否开启。"
    elif any(
        marker in combined
        for marker in (
            "connection timed out",
            "operation timed out",
            "no route to host",
            "network is unreachable",
        )
    ):
        summary = "RTSP 连接超时：请确认摄像头在线，并且当前主机能够访问摄像头所在网络。"
    elif any(
        marker in combined
        for marker in (
            "name or service not known",
            "nodename nor servname provided",
            "failed to resolve hostname",
            "temporary failure in name resolution",
        )
    ):
        summary = "无法解析 RTSP 主机名：请检查地址中的主机名或 IP。"
    elif "no complete jpeg frame" in combined:
        summary = (
            f"RTSP 已连接，但 {timeout_seconds:g} 秒内没有收到完整视频帧："
            "摄像头可能卡流、网络中断或该地址没有视频轨道。"
        )
    elif any(
        marker in combined
        for marker in (
            "invalid data found when processing input",
            "could not find codec parameters",
            "unsupported codec",
        )
    ):
        summary = "已连接 RTSP，但无法解析视频流：请检查码流地址和摄像头视频编码设置。"
    elif "protocol not found" in combined:
        summary = "当前 FFmpeg 不支持该 RTSP 协议或地址格式。"
    elif any(marker in combined for marker in ("end of file", "input/output error")):
        summary = "RTSP 连接已中断，未能取得完整视频帧。"
    else:
        summary = "无法连接或读取 RTSP 视频流：请检查地址、凭据、网络和摄像头状态。"

    if diagnostic:
        compact = " ".join(diagnostic.split())
        if len(compact) > 240:
            compact = compact[:237] + "..."
        return f"{summary} 技术信息：{compact}"
    return summary


def build_ffmpeg_command(
    binary: str,
    rtsp_url: str,
    transport: str,
    io_timeout_seconds: float = 30.0,
) -> list[str]:
    io_timeout_microseconds = max(1, int(io_timeout_seconds * 1_000_000))
    return [
        binary,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-timeout",
        str(io_timeout_microseconds),
        "-rtsp_transport",
        transport,
        "-i",
        rtsp_url,
        "-an",
        "-q:v",
        "4",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "pipe:1",
    ]


async def _read_mjpeg_frames_unbounded(reader: asyncio.StreamReader) -> AsyncIterator[bytes]:
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


async def read_mjpeg_frames(
    reader: asyncio.StreamReader,
    frame_timeout_seconds: float | None = None,
) -> AsyncIterator[bytes]:
    """Yield complete JPEGs and optionally fail when complete-frame output stalls."""

    frames = _read_mjpeg_frames_unbounded(reader)
    while True:
        try:
            if frame_timeout_seconds is None:
                frame = await anext(frames)
            else:
                frame = await asyncio.wait_for(anext(frames), timeout=frame_timeout_seconds)
        except StopAsyncIteration:
            return
        except TimeoutError as exc:
            raise FrameReadTimeout(
                f"FFmpeg produced no complete JPEG frame for {frame_timeout_seconds:g} seconds"
            ) from exc
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
