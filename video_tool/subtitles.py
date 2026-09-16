# -*- coding: utf-8 -*-
"""字幕轨选择、SRT 导出与解析

目标视频的字幕是内嵌软字幕（独立字幕轨道），因此不需要 OCR：直接从字幕轨
拿到「哪一句台词、从第几秒说到第几秒」，再据此定位画面，精确且零识别误差。
"""

import os
import re
from dataclasses import dataclass

from . import config, ffmpeg_utils
from .config import VideoToolError


@dataclass
class SubtitleCue:
    """一句台词。"""

    index: int      # 句序，从 1 开始
    start: float    # 起始时间（秒）
    end: float      # 结束时间（秒）
    text: str       # 清洗后的台词文本，可含换行

    @property
    def duration(self):
        """该句台词持续时长（秒）。"""
        return max(0.0, self.end - self.start)

    @property
    def timecode(self):
        """起始时间码，形如 00:12:34.567。"""
        return format_timecode(self.start)


def format_timecode(seconds):
    """把秒数格式化为 HH:MM:SS.mmm。"""
    seconds = max(0.0, float(seconds or 0.0))
    whole = int(seconds)
    millis = int(round((seconds - whole) * 1000))
    if millis >= 1000:
        millis -= 1000
        whole += 1
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    return '%02d:%02d:%02d.%03d' % (hours, minutes, secs, millis)


# ---------------------------------------------------------------------------
# 字幕轨选择
# ---------------------------------------------------------------------------
def stream_title(stream):
    """取字幕轨的可读名称（优先 TrackName，其次 handler_name）。"""
    tags = stream.get('tags') or {}
    for key in ('title', 'handler_name', 'filename'):
        value = tags.get(key)
        if value:
            return str(value).strip()
    return '未命名字幕轨'


def _is_text_subtitle(stream):
    codec = (stream.get('codec_name') or '').lower()
    if codec in config.TEXT_SUBTITLE_CODECS:
        return True
    # 少数封装把编码写在 tags 中
    codec_tag = ((stream.get('tags') or {}).get('codec_id') or '').lower()
    return codec_tag in config.TEXT_SUBTITLE_CODECS


def select_subtitle_stream(streams, track_selector='auto'):
    """从轨道列表中挑选用于台词定位的字幕轨。

    track_selector 为 'auto' 时自动选择（简体优先）；也可传入轨名关键字。
    返回 (stream, 选择说明)，找不到时返回 (None, 说明)。
    """
    subtitle_streams = [s for s in streams if s.get('codec_type') == 'subtitle']
    if not subtitle_streams:
        return None, '视频中未找到字幕轨'

    text_streams = [s for s in subtitle_streams if _is_text_subtitle(s)]
    if not text_streams:
        return None, '字幕轨均为图像字幕（PGS/VobSub），无法提取文本台词'

    prefix = ''
    if track_selector and track_selector != 'auto':
        for stream in text_streams:
            if track_selector.lower() in stream_title(stream).lower():
                return stream, '按指定字幕轨「%s」' % stream_title(stream)
        prefix = '未找到名为「%s」的字幕轨，已自动选择：' % track_selector

    # 1) 按轨名关键字（简体优先）
    for keyword in config.SUBTITLE_NAME_PREFERENCE:
        for stream in text_streams:
            if keyword.lower() in stream_title(stream).lower():
                return stream, prefix + '按轨名匹配「%s」' % stream_title(stream)

    # 2) 按语言码兜底
    for lang in config.SUBTITLE_LANG_PREFERENCE:
        for stream in text_streams:
            language = ((stream.get('tags') or {}).get('language') or '').lower()
            if language == lang:
                return stream, prefix + '按语言码 %s 匹配「%s」' % (lang, stream_title(stream))

    return text_streams[0], prefix + '回退到第一条文本字幕轨「%s」' % stream_title(text_streams[0])


def list_subtitle_streams(streams):
    """列出字幕轨概要，供界面展示。"""
    result = []
    for stream in streams:
        if stream.get('codec_type') != 'subtitle':
            continue
        tags = stream.get('tags') or {}
        result.append({
            'index': stream.get('index'),
            'title': stream_title(stream),
            'language': tags.get('language') or '',
            'codec': stream.get('codec_name') or '',
            'text': _is_text_subtitle(stream),
        })
    return result


# ---------------------------------------------------------------------------
# SRT 解析
# ---------------------------------------------------------------------------
_TIME_RE = re.compile(
    r'(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*'
    r'(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})'
)
_TAG_RE = re.compile(r'\{[^{}]*\}|<[^<>]*>|\\[Nn]')
_ENTITY_RE = re.compile(r'&(nbsp|amp|lt|gt|quot|#\d+);', re.IGNORECASE)


def _to_seconds(hours, minutes, seconds, millis):
    return (int(hours) * 3600 + int(minutes) * 60 + int(seconds)
            + int(str(millis).ljust(3, '0')[:3]) / 1000.0)


def _read_text(path):
    """按常见中文编码顺序尝试解码字幕文件。"""
    with open(path, 'rb') as handle:
        raw = handle.read()
    for encoding in config.SRT_ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', 'replace')


def clean_text(raw):
    """清洗台词文本：去样式标记、HTML 标签，规整空白与换行。"""
    text = raw.replace('\\N', '\n').replace('\\n', '\n')
    text = _TAG_RE.sub('', text)
    text = _ENTITY_RE.sub(' ', text)
    lines = []
    for line in text.split('\n'):
        line = re.sub(r'[ \t\u3000]+', ' ', line).strip()
        if line:
            lines.append(line)
    return '\n'.join(lines).strip()


def parse_srt(path):
    """解析 SRT 文件为台词列表（已清洗并按时间排序）。"""
    content = _read_text(path).replace('\r\n', '\n').replace('\r', '\n')
    cues = []
    for block in re.split(r'\n\s*\n', content):
        match = _TIME_RE.search(block)
        if not match:
            continue

        lines = [line for line in block.split('\n') if line.strip()]
        text_lines = []
        time_seen = False
        for line in lines:
            if _TIME_RE.search(line):
                time_seen = True
                continue
            if time_seen:
                text_lines.append(line)

        text = clean_text('\n'.join(text_lines))
        if not text:
            continue

        start = _to_seconds(*match.group(1, 2, 3, 4))
        end = _to_seconds(*match.group(5, 6, 7, 8))
        if end <= start:
            end = start + config.MIN_CUE_DURATION
        cues.append(SubtitleCue(0, start, end, text))

    cues.sort(key=lambda cue: cue.start)
    return merge_cues(cues)


def merge_cues(cues, min_duration=None, merge_gap=None,
               max_duration=None, max_lines=None):
    """合并碎片台词与滚动重复字幕，并重新编号。

    合并的动机是避免同一句话被切成多个十几毫秒的碎片（候选帧区间没有意义）。
    但本片源的对白是连续不断的（前一句结束时间等于后一句开始时间，间隔为 0），
    若仅按「间隔小」就无限合并，会把整段对白粘成一句十几秒、五六行的内容，
    既违背「每句台词出一张图」的预期，也超出图片底部的可容纳行数。

    因此这里给合并加了上限：合并后的时长与文本行数不得超限；只有「碎片」
    （时长不足）与「文字完全相同的滚动字幕」才无条件合并。
    """
    min_duration = config.MIN_CUE_DURATION if min_duration is None else min_duration
    merge_gap = config.MERGE_GAP if merge_gap is None else merge_gap
    max_duration = config.MAX_CUE_DURATION if max_duration is None else max_duration
    max_lines = config.MAX_CUE_TEXT_LINES if max_lines is None else max_lines

    merged = []
    for cue in cues:
        if merged:
            previous = merged[-1]
            same_text = previous.text == cue.text
            too_short = previous.duration < min_duration
            too_close = (cue.start - previous.end) <= merge_gap

            if same_text or too_short or too_close:
                new_end = max(previous.end, cue.end)
                if cue.text and cue.text not in previous.text:
                    new_text = (previous.text + '\n' + cue.text).strip()
                else:
                    new_text = previous.text

                within_limit = (
                    (new_end - previous.start) <= max_duration
                    and len(new_text.split('\n')) <= max_lines
                )
                # 碎片与重复字幕必须并掉；单纯「挨得近」的独立台词则受上限约束
                if within_limit or too_short or same_text:
                    previous.end = new_end
                    previous.text = new_text
                    continue

        merged.append(SubtitleCue(len(merged) + 1, cue.start, cue.end, cue.text))

    for position, cue in enumerate(merged, start=1):
        cue.index = position
    return merged


def load_cues(video_path, streams, track_selector, work_dir):
    """一步到位：选中字幕轨 -> 导出 SRT -> 解析为台词列表。

    返回 (cues, 字幕轨信息文本)。
    """
    stream, reason = select_subtitle_stream(streams, track_selector)
    if stream is None:
        raise VideoToolError('无法定位字幕轨：%s' % reason)

    srt_path = os.path.join(work_dir, 'subtitle.srt')
    ffmpeg_utils.extract_subtitle(video_path, stream.get('index'), srt_path)
    cues = parse_srt(srt_path)

    info = '0:%s「%s」(%s) - %s' % (
        stream.get('index'), stream_title(stream),
        stream.get('codec_name') or '未知编码', reason,
    )
    return cues, info
