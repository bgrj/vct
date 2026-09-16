# -*- coding: utf-8 -*-
"""FFmpeg / FFprobe 调用封装

把与外部可执行文件打交道的细节集中在这里：可执行文件查找、超时控制、
错误包装。上层只关心「拿到轨道信息」「拿到候选帧」「拿到字幕文件」，
不直接拼装命令行，便于统一维护与排错。
"""

import json
import os
import shutil
import subprocess

from . import config
from .config import VideoToolError


class FFmpegError(VideoToolError):
    """FFmpeg / FFprobe 调用失败。"""


_cached_paths = {}


def _find_executable(name):
    """按「配置候选目录 -> 系统 PATH」的顺序查找可执行文件。"""
    exe_name = name + ('.exe' if os.name == 'nt' else '')
    for directory in config.FFMPEG_SEARCH_DIRS:
        candidate = os.path.join(directory, exe_name)
        if os.path.isfile(candidate):
            return candidate
    return shutil.which(name)


def _resolve(name):
    if name not in _cached_paths:
        path = _find_executable(name)
        if not path:
            raise FFmpegError(
                '未找到 %s。请安装后加入系统 PATH，或放到 %s 目录下。'
                % (name, config.FFMPEG_SEARCH_DIRS[0])
            )
        _cached_paths[name] = path
    return _cached_paths[name]


def ffmpeg_path():
    """返回 ffmpeg 可执行文件路径。"""
    return _resolve('ffmpeg')


def ffprobe_path():
    """返回 ffprobe 可执行文件路径。"""
    return _resolve('ffprobe')


def _run(cmd, timeout):
    """执行外部命令，失败时抛出携带 ffmpeg 错误摘要的 FFmpegError。"""
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        raise FFmpegError('命令执行超时（%s 秒）：%s' % (timeout, os.path.basename(cmd[0])))
    except OSError as exc:
        raise FFmpegError('无法执行 %s：%s' % (cmd[0], exc))

    if proc.returncode != 0:
        message = proc.stderr.decode('utf-8', 'replace').strip()
        tail = message.splitlines()[-1] if message else ''
        raise FFmpegError('命令执行失败：%s' % (tail or os.path.basename(cmd[0])))
    return proc


# ---------------------------------------------------------------------------
# 媒体信息
# ---------------------------------------------------------------------------
def probe_media_info(video_path):
    """读取媒体信息（轨道 + 容器），返回 ffprobe 的 JSON 结果。"""
    if not os.path.isfile(video_path):
        raise FFmpegError('文件不存在：%s' % video_path)

    cmd = [
        ffprobe_path(), '-v', 'error',
        '-show_streams', '-show_format',
        '-of', 'json', video_path,
    ]
    proc = _run(cmd, config.FFPROBE_TIMEOUT)
    try:
        return json.loads(proc.stdout.decode('utf-8', 'replace'))
    except ValueError as exc:
        raise FFmpegError('解析 ffprobe 输出失败：%s' % exc)


def probe_streams(video_path):
    """读取全部轨道列表。"""
    return probe_media_info(video_path).get('streams', [])


def probe_duration(video_path):
    """读取视频时长（秒）。"""
    info = probe_media_info(video_path)
    duration = (info.get('format') or {}).get('duration')
    if duration in (None, 'N/A'):
        for stream in info.get('streams', []):
            if stream.get('duration') not in (None, 'N/A'):
                duration = stream['duration']
                break
    try:
        return float(duration)
    except (TypeError, ValueError):
        raise FFmpegError('无法获取视频时长：%s' % os.path.basename(video_path))


def probe_frame_rate(video_path):
    """读取视频帧率，解析形如 "24/1" 的分数形式，失败返回 0。"""
    for stream in probe_streams(video_path):
        if stream.get('codec_type') == 'video':
            raw = stream.get('r_frame_rate') or ''
            if '/' in raw:
                num, _, den = raw.partition('/')
                try:
                    return float(num) / float(den) if float(den) else 0.0
                except ValueError:
                    return 0.0
            try:
                return float(raw)
            except ValueError:
                return 0.0
    return 0.0


# ---------------------------------------------------------------------------
# 字幕与候选帧
# ---------------------------------------------------------------------------
def extract_subtitle(video_path, stream_index, out_srt):
    """把指定字幕轨导出为 SRT 文件。

    stream_index 为 ffprobe 给出的绝对轨道序号（0:index）。
    """
    cmd = [
        ffmpeg_path(), '-y', '-v', 'error',
        '-i', video_path,
        '-map', '0:%d' % stream_index,
        '-c:s', 'srt',
        out_srt,
    ]
    _run(cmd, config.FFMPEG_SUBTITLE_TIMEOUT)

    if not os.path.isfile(out_srt) or os.path.getsize(out_srt) == 0:
        raise FFmpegError('字幕轨 0:%d 导出结果为空' % stream_index)
    return out_srt


def sample_frames(video_path, start, duration, fps, out_pattern, timeout=None):
    """在 [start, start+duration] 区间内按 fps 采样候选帧。

    返回按序号排序的图片文件列表。只调用一次 ffmpeg，避免逐帧启动进程。
    """
    cmd = [
        ffmpeg_path(), '-y', '-v', 'error',
        '-ss', '%.3f' % max(0.0, float(start)),
        '-i', video_path,
        '-t', '%.3f' % max(0.05, float(duration)),
        '-vf', 'fps=%.4f' % max(0.01, float(fps)),
        '-q:v', '2',
        out_pattern,
    ]
    _run(cmd, timeout or config.FFMPEG_FRAME_TIMEOUT)

    directory = os.path.dirname(out_pattern) or '.'
    prefix = os.path.basename(out_pattern).split('%')[0]
    files = [
        os.path.join(directory, name)
        for name in os.listdir(directory)
        if name.startswith(prefix) and name.lower().endswith(('.jpg', '.jpeg', '.png'))
    ]
    return sorted(files)


# ---------------------------------------------------------------------------
# 文件类型判断
# ---------------------------------------------------------------------------
def is_video_file(file_path):
    """检查文件是否是视频文件：先按扩展名快速过滤，再用 ffprobe 确认。"""
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in config.VIDEO_EXTENSIONS:
        return False
    try:
        cmd = [
            ffprobe_path(), '-v', 'error',
            '-show_entries', 'format=format_name',
            '-of', 'default=noprint_wrappers=1:nokey=1', file_path,
        ]
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=config.FFPROBE_TIMEOUT,
        )
    except (VideoToolError, subprocess.SubprocessError, OSError):
        return False
    return proc.returncode == 0 and bool(proc.stdout.decode('utf-8', 'replace').strip())


def is_incomplete_file(file_path):
    """判断是否为下载工具产生的「未下载完成」文件。"""
    lower_name = os.path.basename(file_path).lower()
    return any(lower_name.endswith(ext) for ext in config.INCOMPLETE_EXTS)


def describe_incomplete(file_path):
    """给出未完成文件的可读描述，例如 xxx.mkv（.qkdownloading，已下载 1.4 GB）。"""
    name = os.path.basename(file_path)
    lower_name = name.lower()
    ext = ''
    for candidate in config.INCOMPLETE_EXTS:
        if lower_name.endswith(candidate):
            ext = candidate
            break
    real_name = name[:-len(ext)] if ext else name
    try:
        size = os.path.getsize(file_path)
        if size >= 1024 ** 3:
            size_text = '%.2f GB' % (size / 1024.0 ** 3)
        else:
            size_text = '%.1f MB' % (size / 1024.0 ** 2)
    except OSError:
        size_text = '大小未知'
    return '%s（%s 未下载完成，当前 %s）' % (real_name, ext or '临时后缀', size_text)
