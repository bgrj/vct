# -*- coding: utf-8 -*-
"""台词截图主流程

扫描输入目录 -> 推导输出目录（<集号>-pc）-> 选中文字幕轨并解析台词 ->
逐句采样候选帧 -> 打分选优 -> 渲染台词并落盘 -> 汇总索引与报告。

设计要点：
* 一次只处理一集，便于逐集验收；
* 断点续跑：该句图片已存在则跳过，中断后重跑不重复劳动；
* 单句失败不中断整集，错误逐条记入报告；
* 下载未完成的文件在扫描阶段就跳过，并说明原因。
"""

import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field, replace as dataclass_replace

from . import config, ffmpeg_utils, quality, renderer, style_profile, subtitles
from .config import VideoToolError

_EPISODE_RE = re.compile(r'[Ss](\d{1,2})[Ee](\d{1,3})')

# quality 给出的说话状态（英文枚举）到索引表中文文案的映射
_SPEAKING_TEXT = {
    'speaking': '正在说话',
    'idle': '闭嘴',
    'open': '张口过大',
}

# 眼睛状态（英文枚举）到索引表中文文案的映射；无人像/无法判定时留空
_EYE_TEXT = {
    'open': '睁开',
    'half': '半闭',
    'closed': '闭合',
}

# 上表的反向映射：从索引行读回眼睛状态，用于判断哪些句子需要重采样
_EYE_TEXT_STATE = {text: state for state, text in _EYE_TEXT.items()}


# ---------------------------------------------------------------------------
# 回调
# ---------------------------------------------------------------------------
class Reporter:
    """进度与状态回调的默认实现；GUI 模式传入自定义实现以刷新界面。"""

    def status(self, text):
        """上报一条状态文本。"""
        print(text)

    def progress(self, value):
        """上报 0~100 的进度值。"""

    def cancelled(self):
        """是否被用户取消。"""
        return False


class SilentReporter(Reporter):
    """静默回调，用于自动化测试。"""

    def status(self, text):
        pass


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class DialogueOptions:
    """台词截图模式的可调参数。"""

    subtitle_track: str = 'auto'      # 'auto' 或字幕轨名称关键字
    candidate_min: int = config.CANDIDATE_MIN
    candidate_max: int = config.CANDIDATE_MAX
    candidate_fps: float = config.CANDIDATE_FPS
    burn_subtitle: bool = True        # 是否把台词渲染到图片底部
    start_cue: int = 1                # 从第几句开始（便于抽样试跑）
    max_cues: int = 0                 # 最多处理几句，0 表示全部
    resume: bool = True               # 断点续跑：已存在图片则跳过
    clean: bool = False               # 重建：先清空输出目录中的历史图片与索引
    eye_refine: str = 'closed'        # 闭眼重采样口径：closed / half / off
    write_index: bool = True
    write_report: bool = True


@dataclass
class EpisodeResult:
    """单集处理结果。"""

    video: str = ''
    output_dir: str = ''
    subtitle_info: str = ''
    total_cues: int = 0
    planned_cues: int = 0
    success: int = 0
    degraded: int = 0
    skipped: int = 0
    failed: int = 0
    elapsed: float = 0.0
    rows: list = field(default_factory=list)
    backfill_rows: list = field(default_factory=list)   # 断点续跑跳过、需补回索引的记录
    errors: list = field(default_factory=list)
    face_backend: str = ''
    style_source: str = ''            # 样式基线来源（参考素材统计结果）
    cleaned_images: int = 0           # 重建时清理掉的旧图片数量
    integrity_missing: int = 0        # 完整性校验发现缺失的句数
    refilled: int = 0                 # 校验后补跑补齐的句数
    eye_open: int = 0                 # 出图帧中眼睛睁开（合格）的句数
    eye_half: int = 0                 # 出图帧中眼睛半闭的句数（含单眼微闭）
    eye_closed: int = 0               # 出图帧中眼睛闭合的句数（整句无更好选择时的兜底）
    eye_unknown: int = 0              # 无有效人脸、无法判定眼睛状态的句数
    eye_refined: int = 0              # 因闭眼/半闭眼而重采样、且挑到更好一帧的句数
    eye_refine_tried: int = 0         # 触发重采样的句数（含重采样后仍无改善的）


@dataclass
class RunResult:
    """整次运行结果。"""

    input_dir: str = ''
    episodes: list = field(default_factory=list)
    incomplete_files: list = field(default_factory=list)
    unreadable_files: list = field(default_factory=list)

    @property
    def success(self):
        return sum(item.success for item in self.episodes)

    @property
    def degraded(self):
        return sum(item.degraded for item in self.episodes)

    @property
    def failed(self):
        return sum(item.failed for item in self.episodes)

    @property
    def skipped(self):
        return sum(item.skipped for item in self.episodes)


# ---------------------------------------------------------------------------
# 路径与命名
# ---------------------------------------------------------------------------
def episode_tag(video_path):
    """从文件名提取集号标签，如 Love...S01E03....mkv -> S01E03。"""
    stem = os.path.splitext(os.path.basename(video_path))[0]
    match = _EPISODE_RE.search(stem)
    if match:
        return 'S%02dE%02d' % (int(match.group(1)), int(match.group(2)))
    return stem or 'video'


def clean_output(output_dir, episode=None):
    """清空输出目录中本工具产出的历史图片与索引（重建模式使用）。

    只删自己产出的文件，用户放在其中的其他文件保持不动；返回删除数量。
    """
    if not os.path.isdir(output_dir):
        return 0
    removed = 0
    suffix = '.' + config.IMAGE_FORMAT.lower().lstrip('.')
    for name in sorted(os.listdir(output_dir)):
        path = os.path.join(output_dir, name)
        if not os.path.isfile(path):
            continue
        is_image = name.lower().endswith(suffix)
        is_meta = name in (config.INDEX_CSV_NAME, config.REPORT_NAME)
        if not (is_image or is_meta):
            continue
        if episode and is_image and not name.startswith(episode):
            continue
        try:
            os.remove(path)
            if is_image:
                removed += 1
        except OSError:
            continue
    return removed


def derive_output_dir(video_dir):
    """按「视频所在目录名 + -pc」推导输出目录。

    例：...\\Love in the big City\\03 -> ...\\Love in the big City\\03\\03-pc
    """
    video_dir = os.path.abspath(video_dir)
    return os.path.join(video_dir, os.path.basename(video_dir) + config.OUTPUT_SUFFIX)


# ---------------------------------------------------------------------------
# 目录扫描
# ---------------------------------------------------------------------------
def scan_directory(input_dir, max_depth=10, reporter=None):
    """递归扫描输入目录。

    返回 (视频文件列表, 未完成文件列表, 无法识别的文件列表)。
    """
    reporter = reporter or SilentReporter()
    videos, incomplete, unreadable = [], [], []
    base_depth = input_dir.rstrip('\\/').count(os.sep)

    for current_dir, dir_names, file_names in os.walk(input_dir):
        depth = current_dir.rstrip('\\/').count(os.sep) - base_depth
        if depth >= max_depth:
            dir_names[:] = []
            continue

        # 跳过输出目录自身，避免把历史产物再扫一遍
        dir_names[:] = [
            name for name in dir_names
            if not name.endswith(config.OUTPUT_SUFFIX)
        ]

        for file_name in file_names:
            path = os.path.join(current_dir, file_name)
            if os.path.islink(path):
                continue

            if ffmpeg_utils.is_incomplete_file(path):
                incomplete.append(path)
                reporter.status('跳过未下载完成的文件：%s' % file_name)
                continue

            extension = os.path.splitext(file_name)[1].lower()
            if extension not in config.VIDEO_EXTENSIONS:
                continue

            if ffmpeg_utils.is_video_file(path):
                videos.append(path)
            else:
                unreadable.append(path)

    videos.sort()
    incomplete.sort()
    unreadable.sort()
    return videos, incomplete, unreadable


# ---------------------------------------------------------------------------
# 单集处理
# ---------------------------------------------------------------------------
def _sample_candidates(video_path, cue, options, work_dir):
    """在台词区间内采样候选帧，返回 [(路径, 时间点秒), ...]。"""
    duration = max(cue.duration, 0.12)
    desired = int(round(duration * options.candidate_fps))
    desired = max(options.candidate_min, min(options.candidate_max, desired))
    fps = desired / duration

    # 清掉上一句的候选帧，避免混入本次打分
    for name in os.listdir(work_dir):
        if name.startswith('cand_'):
            try:
                os.remove(os.path.join(work_dir, name))
            except OSError:
                pass

    pattern = os.path.join(work_dir, 'cand_%03d.jpg')
    files = ffmpeg_utils.sample_frames(video_path, cue.start, duration, fps, pattern)

    candidates = []
    for position, path in enumerate(files):
        timestamp = cue.start + position / fps
        timestamp = min(max(timestamp, cue.start), cue.end)
        candidates.append((path, timestamp))
    return candidates


def _backfill_row(image_path, cue):
    """为跳过（断点续跑）的台词生成索引记录：只填已知信息，不伪造分数。"""
    name = os.path.basename(image_path)
    stamp = renderer.parse_image_timecode(name)
    return {
        'index': cue.index,
        'start_timecode': subtitles.format_timecode(cue.start),
        'end_timecode': subtitles.format_timecode(cue.end),
        'duration': round(cue.duration, 3),
        'text': cue.text,
        'image': name,
        'frame_timecode': '' if stamp is None else renderer.format_timecode(stamp),
        'total': '',
        'sharpness': '',
        'motion_penalty': '',
        'face_count': '',
        'eyes_open': '',
        'eye_state_text': '',
        'mouth_natural': '',
        'speaking': '',
        'composition': '',
        'timing': '',
        'shot': '',
        'frame_type': '',
    }


def _format_eta(elapsed, done, total):
    """根据已用时间估算剩余时间。"""
    if done <= 0 or total <= 0:
        return ''
    remaining = elapsed / done * (total - done)
    if remaining < 60:
        return '约 %d 秒' % int(remaining)
    return '约 %.1f 分钟' % (remaining / 60.0)


def _count_eye_state(result, best):
    """累计出图帧的眼睛状态，供报告与汇总展示。

    眼睛比嘴型更容易被一眼看出问题，因此报告里单独统计「睁开 / 半闭 / 闭合 /
    无人像」，方便快速判断这批图有没有明显闭眼的废片。
    """
    _add_eye_state(result, best.eye_state, 1)


def _add_eye_state(result, state, delta):
    """按眼睛状态增减计数；重采样换图后需要用负的 delta 回退旧计数。"""
    state = (state or '')
    if state == 'open':
        result.eye_open += delta
    elif state == 'half':
        result.eye_half += delta
    elif state == 'closed':
        result.eye_closed += delta
    else:
        result.eye_unknown += delta


def _index_row(cue, best, image_name):
    """把一句台词的处理结果整理成索引行（列与 renderer.INDEX_HEADER 对齐）。"""
    detail = best.detail or {}
    return {
        'index': cue.index,
        'start_timecode': subtitles.format_timecode(cue.start),
        'end_timecode': subtitles.format_timecode(cue.end),
        'duration': round(cue.duration, 3),
        'text': cue.text,
        'image': image_name,
        'frame_timecode': renderer.format_timecode(best.timestamp),
        'total': best.total,
        'sharpness': round(best.sharpness, 1),
        'motion_penalty': best.motion_penalty,
        'face_count': best.face_count,
        'eyes_open': ('' if 'eyes_closed' not in detail
                      else ('否' if detail['eyes_closed'] else '是')),
        'eye_state_text': _EYE_TEXT.get(detail.get('eye_state', ''), ''),
        'mouth_natural': ('' if 'mouth_too_open' not in detail
                          else ('否' if detail['mouth_too_open'] else '是')),
        'speaking': _SPEAKING_TEXT.get(detail.get('speaking', ''), ''),
        'composition': best.composition_score,
        'timing': best.timing_score,
        'shot': best.shot_id,
        'frame_type': '平坦画面' if best.flat else '正常画面',
        'degraded': best.degraded,
    }


def _process_cue(video_path, cue, options, analyzer, work_dir, output_dir, episode):
    """处理单句台词：采样 -> 打分选优 -> 渲染落盘。

    返回 (索引行, 最优帧)；失败时抛 VideoToolError。抽成独立函数是为了让
    完整性校验的补跑复用同一套流程，避免两处逻辑走偏。
    """
    candidates = _sample_candidates(video_path, cue, options, work_dir)
    if not candidates:
        raise VideoToolError('未能抽取到候选帧')

    scores = quality.score_candidates(candidates, analyzer)
    best = quality.select_best(scores)
    if best is None:
        raise VideoToolError('候选帧全部读取失败')

    image_name = renderer.build_image_name(episode, cue.index, best.timestamp)
    image_path = os.path.join(output_dir, image_name)
    if options.burn_subtitle:
        renderer.render_subtitle(best.path, image_path, cue.text)
    else:
        shutil.copyfile(best.path, image_path)

    return _index_row(cue, best, image_name), best


def _integrity_state(output_dir, episode, selected, options):
    """完整性校验的三量读数。

    返回 (应有句序集合, 目录已有句序集合, 缺失台词列表, 索引行数)。
    「三量」即字幕句数、目录内图片数、索引表行数：断点续跑、单句失败、
    历史残留都会让这三者对不上，收尾时据此判定是否需要补跑。
    """
    expected = {cue.index for cue in selected}
    present = renderer.list_image_indexes(output_dir, episode)
    missing = [cue for cue in selected if cue.index not in present]
    index_path = os.path.join(output_dir, config.INDEX_CSV_NAME)
    indexed = renderer.count_index_rows(index_path) if options.write_index else 0
    return expected, present, missing, indexed


def _refill_missing_cues(video_path, missing, options, analyzer, work_dir, output_dir,
                         episode, result, failed_indexes, reporter):
    """把「有台词却没有图片」的句子补跑一遍，返回补跑成功的句数。

    复用 _process_cue，保证补跑与正常流程完全同一条链路；补跑成功会把该句从
    失败集合中移除，避免报告里「失败句数」被重复计数。
    """
    refilled = 0
    for order, cue in enumerate(missing, start=1):
        if reporter.cancelled():
            reporter.status('已取消，剩余缺失台词未补跑')
            break

        prefix = '补跑 %d/%d（第 %d 句）' % (order, len(missing), cue.index)
        try:
            row, best = _process_cue(video_path, cue, options, analyzer,
                                     work_dir, output_dir, episode)
            result.rows.append(row)
            result.success += 1
            if best.degraded:
                result.degraded += 1
            _count_eye_state(result, best)
            failed_indexes.discard(cue.index)
            refilled += 1
            reporter.status('%s 完成：%s' % (prefix, row['image']))
        except VideoToolError as exc:
            failed_indexes.add(cue.index)
            result.errors.append('第 %d 句（补跑）：%s' % (cue.index, exc))
            reporter.status('%s 失败：%s' % (prefix, exc))
        except Exception as exc:                      # 兜底，绝不中断整集
            failed_indexes.add(cue.index)
            result.errors.append('第 %d 句（补跑）：未预期错误 %s' % (cue.index, exc))
            reporter.status('%s 出现未预期错误：%s' % (prefix, exc))
    return refilled


def _remove_quietly(path):
    """删除文件，不存在或无权限时静默跳过（收尾清理不应影响主流程）。"""
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def _replace_row(result, old_row, new_row):
    """把索引行列表中的旧记录替换为新记录（按行对象身份定位）。"""
    for position, row in enumerate(result.rows):
        if row is old_row:
            result.rows[position] = new_row
            return True
    return False


def _retry_options(options):
    """重采样用的参数副本：只提高候选帧数与采样密度，其余口径与正常流程一致。"""
    return dataclass_replace(
        options,
        candidate_max=max(options.candidate_max, config.EYE_REFINE_CANDIDATE_MAX),
        candidate_fps=max(options.candidate_fps, config.EYE_REFINE_CANDIDATE_FPS),
    )


def _eye_refine_targets(result, cues_by_index, mode):
    """挑出需要重采样的台词：本次出图帧的眼睛状态落在重采样口径内。"""
    targets = []
    for row in result.rows:
        state = _EYE_TEXT_STATE.get(row.get('eye_state_text', ''), '')
        if not quality.eye_needs_refine(state, mode):
            continue
        cue = cues_by_index.get(row['index'])
        if cue is not None:
            targets.append((cue, row, state))
    return targets


def _eye_refine(video_path, cues_by_index, options, analyzer, work_dir, output_dir,
                episode, result, reporter):
    """对「整句候选都没睁眼」的句子提高候选帧数重采样一次，尽量换成睁眼帧。

    触发依据是本次出图帧的眼睛状态：quality.select_best 已把闭眼帧排除在候选池外，
    仍被选中说明这一句的所有候选帧都不合格（多为眨眼瞬间恰好被采样命中）。这类
    句子每集通常只有个位数，把候选帧数与采样密度临时提高再挑一次的成本可以忽略，
    却能明显减少闭眼废片。

    重采样后只有在「眼睛状态确实变好」时才替换图片与索引行；未变好则删除新图、
    保留原图，保证一句仍然只有一张图。
    """
    mode = options.eye_refine or 'off'
    if mode not in config.EYE_REFINE_MODES:
        mode = 'closed'
    if mode == 'off':
        return 0

    targets = _eye_refine_targets(result, cues_by_index, mode)
    result.eye_refine_tried = len(targets)
    if not targets:
        return 0

    retry_options = _retry_options(options)
    refined = 0
    for order, (cue, row, state) in enumerate(targets, start=1):
        if reporter.cancelled():
            reporter.status('已取消，剩余闭眼句未重采样')
            break

        prefix = '重采样 %d/%d（第 %d 句，原状态：%s）' % (
            order, len(targets), cue.index, _EYE_TEXT.get(state, '未判定'))

        try:
            new_row, best = _process_cue(video_path, cue, retry_options, analyzer,
                                         work_dir, output_dir, episode)
        except Exception as exc:                      # 兜底，绝不中断整集
            result.errors.append('第 %d 句（重采样）：%s' % (cue.index, exc))
            reporter.status('%s 失败：%s' % (prefix, exc))
            continue

        improved = quality.eye_rank(best.eye_state) > quality.eye_rank(state)
        if not improved:
            # 重采样没挑到更好的帧：删掉这张多余的新图，保留原图，避免一句两张
            if new_row['image'] != row['image']:
                _remove_quietly(os.path.join(output_dir, new_row['image']))
            reporter.status('%s 无改善，保留原图' % prefix)
            continue

        if new_row['image'] != row['image']:
            _remove_quietly(os.path.join(output_dir, row['image']))
        _replace_row(result, row, new_row)

        _add_eye_state(result, state, -1)
        _add_eye_state(result, best.eye_state, 1)
        if best.degraded and not row.get('degraded'):
            result.degraded += 1
        elif row.get('degraded') and not best.degraded:
            result.degraded -= 1

        refined += 1
        reporter.status('%s 已更换：%s（眼睛 %s，得分 %.2f）' % (
            prefix, new_row['image'],
            _EYE_TEXT.get(best.eye_state or '', '未判定'), best.total))

    return refined


def process_video(video_path, options=None, reporter=None):
    """处理单个视频：解析台词 -> 逐句选优 -> 渲染落盘 -> 输出索引与报告。"""
    options = options or DialogueOptions()
    reporter = reporter or SilentReporter()

    output_dir = derive_output_dir(os.path.dirname(video_path))
    os.makedirs(output_dir, exist_ok=True)

    result = EpisodeResult(video=video_path, output_dir=output_dir)
    started = time.time()
    # 候选帧放系统临时目录：输出目录里若堆上几百张临时帧，容易被清理工具误删，
    # 也会让 -pc 目录在浏览器里出现几百张一闪而过的图片。
    work_dir = tempfile.mkdtemp(prefix='video_tool_cand_')

    try:
        reporter.status('正在读取轨道信息：%s' % os.path.basename(video_path))
        streams = ffmpeg_utils.probe_streams(video_path)
        if not streams:
            raise VideoToolError('无法读取视频轨道信息')

        reporter.status('正在导出中文字幕轨…')
        cues, subtitle_info = subtitles.load_cues(
            video_path, streams, options.subtitle_track, work_dir
        )
        result.subtitle_info = subtitle_info
        if not cues:
            raise VideoToolError('字幕轨解析后没有任何台词')

        selected = cues[options.start_cue - 1:]
        if options.max_cues and options.max_cues > 0:
            selected = selected[:options.max_cues]
        if not selected:
            raise VideoToolError('指定的句序范围没有台词（共 %d 句）' % len(cues))

        result.total_cues = len(cues)
        result.planned_cues = len(selected)
        reporter.status(
            '共 %d 句台词，本次处理第 %d~%d 句'
            % (len(cues), selected[0].index, selected[-1].index)
        )

        analyzer = quality.FaceAnalyzer()
        result.face_backend = analyzer.describe()
        result.style_source = style_profile.load_style_profile().source
        reporter.status('人像分析：%s' % result.face_backend)
        reporter.status('台词样式基线：%s' % result.style_source)

        episode = episode_tag(video_path)

        if options.clean:
            # 重建：先清掉本集历史图片与旧索引，避免新旧样式混在同一目录里
            result.cleaned_images = clean_output(output_dir, episode)
            reporter.status('重建模式：已清空 %d 张历史图片与旧索引' % result.cleaned_images)

        total = len(selected)
        loop_started = time.time()
        failed_indexes = set()

        for position, cue in enumerate(selected, start=1):
            if reporter.cancelled():
                reporter.status('已取消，剩余台词未处理')
                break

            prefix = '第 %d/%d 句' % (position, total)

            if options.resume:
                existing = renderer.find_existing_image(output_dir, episode, cue.index)
                if existing:
                    result.skipped += 1
                    result.backfill_rows.append(
                        _backfill_row(existing, cue)
                    )
                    reporter.status('%s 已存在，跳过（断点续跑）' % prefix)
                    reporter.progress(position / float(total) * 100.0)
                    continue

            try:
                row, best = _process_cue(video_path, cue, options, analyzer,
                                         work_dir, output_dir, episode)
                result.rows.append(row)
                result.success += 1
                if best.degraded:
                    result.degraded += 1
                _count_eye_state(result, best)

                reporter.status(
                    '%s 完成：%s（得分 %.2f，人脸 %d，眼睛 %s）'
                    % (prefix, row['image'], best.total, best.face_count,
                       _EYE_TEXT.get(best.eye_state or '', '未判定'))
                )
            except VideoToolError as exc:
                failed_indexes.add(cue.index)
                result.errors.append('第 %d 句：%s' % (cue.index, exc))
                reporter.status('%s 失败：%s' % (prefix, exc))
            except Exception as exc:                      # 兜底，绝不中断整集
                failed_indexes.add(cue.index)
                result.errors.append('第 %d 句：未预期错误 %s' % (cue.index, exc))
                reporter.status('%s 出现未预期错误：%s' % (prefix, exc))

            reporter.progress(position / float(total) * 100.0)
            elapsed_loop = time.time() - loop_started
            eta = _format_eta(elapsed_loop, position, total)
            if eta:
                reporter.status('%s 已处理，预计剩余 %s' % (prefix, eta))

        # 闭眼废片重采样：整句候选都没睁眼的句子，提高候选帧数再挑一次
        if options.eye_refine and options.eye_refine != 'off':
            cues_by_index = {cue.index: cue for cue in selected}
            result.eye_refined = _eye_refine(
                video_path, cues_by_index, options, analyzer, work_dir, output_dir,
                episode, result, reporter,
            )
            if result.eye_refine_tried:
                reporter.status(
                    '闭眼重采样：%d 句触发，%d 句换成更好的眼睛状态'
                    % (result.eye_refine_tried, result.eye_refined)
                )

        # 收尾校验：字幕句数 / 目录图片数 / 索引行数三量对齐，缺图的句子补跑
        expected, present, missing, indexed = _integrity_state(
            output_dir, episode, selected, options
        )
        result.integrity_missing = len(missing)
        reporter.status(
            '完整性校验：台词 %d 句，本次应有 %d 张，目录内 %d 张，索引 %d 行'
            % (len(cues), len(expected), len(present & expected), indexed)
        )
        if missing:
            reporter.status('发现 %d 句缺少图片，开始补跑…' % len(missing))
            result.refilled = _refill_missing_cues(
                video_path, missing, options, analyzer, work_dir, output_dir,
                episode, result, failed_indexes, reporter,
            )
            reporter.status('补跑完成：补出 %d 张，仍缺 %d 句'
                            % (result.refilled, len(missing) - result.refilled))
        result.failed = len(failed_indexes)

        if options.write_index:
            index_path = os.path.join(output_dir, config.INDEX_CSV_NAME)
            if result.rows:
                renderer.write_index_csv(result.rows, index_path)
            # 被跳过的台词没有评分数据，但索引表要覆盖整集图片
            if result.backfill_rows:
                renderer.fill_index_gaps(result.backfill_rows, index_path)

        result.elapsed = time.time() - started
        if options.write_report:
            _write_episode_report(result, reporter)

        return result

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _write_episode_report(result, reporter):
    """输出单集处理报告。"""
    # 三量对齐：本次应出图数量（成功 + 跳过 + 补跑）是否覆盖全部台词
    expected_images = result.success + result.skipped + result.refilled
    aligned = (expected_images >= result.total_cues
               and result.integrity_missing == 0
               and result.failed == 0)
    sampled = result.planned_cues < result.total_cues
    if sampled:
        verdict = ('抽样试跑（本次只处理 %d/%d 句，只用于确认样式与画面，'
                   '确认后再全量重跑）' % (result.planned_cues, result.total_cues))
    elif aligned and result.success == 0:
        verdict = '无新增（图片均已存在，如需重出请使用重建模式）'
    elif aligned and result.eye_closed == 0:
        verdict = '通过（图片数 / 索引行数 / 台词句数三方对齐，无闭眼废片）'
    elif aligned and result.eye_closed:
        verdict = ('基本通过（三量对齐；仍有 %d 张闭眼帧：重采样后整句候选依旧无睁眼帧，'
                   '多为低头/闭眼喝水等剧情动作，建议目检）' % result.eye_closed)
    elif aligned:
        verdict = '通过（三量对齐）'
    else:
        verdict = '需人工复核（见上方失败明细与缺失数）'

    lines = [
        '台词智能截图 - 处理报告',
        '=' * 46,
        '视频文件：%s' % os.path.basename(result.video),
        '输出目录：%s' % result.output_dir,
        '字幕轨　：%s' % result.subtitle_info,
        '人像分析：%s' % result.face_backend,
        '样式基线：%s' % (result.style_source or '内置默认值'),
        '',
        '台词总句数：%d' % result.total_cues,
        '本次处理　：%d 句' % result.planned_cues,
        '成功出图　：%d 张' % result.success,
        '其中降级　：%d 张（无人像 / 平坦画面 / 整体低分，属正常保留）' % result.degraded,
        '跳过已存在：%d 张（断点续跑）' % result.skipped,
        '处理失败　：%d 句' % result.failed,
        '耗时　　　：%.1f 秒' % result.elapsed,
        '',
        '完整性校验：',
        '  发现缺失　：%d 句（有台词但目录内没有对应图片）' % result.integrity_missing,
        '  自动补跑　：%d 张' % result.refilled,
        '',
        '人像与眼睛（本次新生成 %d 张）：' % (result.eye_open + result.eye_half
                                              + result.eye_closed + result.eye_unknown),
        '  眼睛睁开　：%d 张' % result.eye_open,
        '  眼睛半闭　：%d 张（含单眼微闭；已优先避开，仅在整句没有更优帧时出现）'
        % result.eye_half,
        '  眼睛闭合　：%d 张（整句候选都闭眼：多为低头/闭眼喝水/闭眼摇头等剧情动作）' % result.eye_closed,
        '  无人像　　：%d 张（空镜 / 背影 / 远景 / 字幕卡，无法判定眼睛）'
        % result.eye_unknown,
        '',
        '逐集验收：',
        '  三量核对　：台词 %d 句 ／ 本次应出图 %d 张（成功 %d + 跳过 %d + 补跑 %d）／ 失败 %d 句'
        % (result.total_cues, expected_images, result.success, result.skipped,
           result.refilled, result.failed),
        '  结论　　　：%s' % verdict,
        '  说明　　　：降级 %d 张为无人像空镜 / 平坦画面，属正常保留；'
        % result.degraded,
        '  　　　　　　目检建议：抽看「有人脸且眼睛状态=睁开」与「无人像」各一张；'
        '若「半闭」偏多，可加 --eye-refine half 后重跑该集（半闭句也重采样）。',
    ]

    if result.eye_refine_tried:
        anchor = next(
            (position for position, text in enumerate(lines) if text.startswith('  无人像')),
            None,
        )
        refine_line = ('  重采样换帧：%d 句（触发 %d 句；提高候选帧数后挑到更好的眼睛状态）'
                       % (result.eye_refined, result.eye_refine_tried))
        if anchor is None:
            lines.append(refine_line)
        else:
            lines.insert(anchor + 1, refine_line)

    if result.cleaned_images:
        lines.insert(8, '重建清理　：%d 张历史图片（含旧索引）' % result.cleaned_images)

    if result.errors:
        lines.append('')
        lines.append('失败明细：')
        lines.extend('  - %s' % item for item in result.errors[:50])
        if len(result.errors) > 50:
            lines.append('  ...（共 %d 条，其余略）' % len(result.errors))

    lines.append('')
    lines.append('提示：再次运行会自动跳过已生成的图片，可直接断点续跑。')

    path = os.path.join(result.output_dir, config.REPORT_NAME)
    renderer.write_report(lines, path)
    reporter.status('报告已写入：%s' % path)


# ---------------------------------------------------------------------------
# 整目录处理
# ---------------------------------------------------------------------------
def run(input_dir, options=None, reporter=None, max_depth=10):
    """处理输入目录中的全部视频，每个视频输出到其所在目录的 <目录名>-pc 下。"""
    options = options or DialogueOptions()
    reporter = reporter or SilentReporter()

    if not os.path.isdir(input_dir):
        raise VideoToolError('输入目录不存在：%s' % input_dir)

    run_result = RunResult(input_dir=input_dir)
    reporter.status('正在扫描目录：%s' % input_dir)

    videos, incomplete, unreadable = scan_directory(input_dir, max_depth, reporter)
    run_result.incomplete_files = incomplete
    run_result.unreadable_files = unreadable

    if not videos:
        reporter.status('未找到可处理的视频文件')
        if incomplete:
            reporter.status(
                '发现 %d 个未下载完成的文件，请先下载完整：' % len(incomplete)
            )
            for path in incomplete:
                reporter.status('  - %s' % ffmpeg_utils.describe_incomplete(path))
        return run_result

    for order, video_path in enumerate(videos, start=1):
        if reporter.cancelled():
            reporter.status('已取消')
            break
        reporter.status(
            '\n=== [%d/%d] 开始处理 %s ===' % (order, len(videos), os.path.basename(video_path))
        )
        try:
            run_result.episodes.append(process_video(video_path, options, reporter))
        except VideoToolError as exc:
            episode = EpisodeResult(video=video_path, output_dir=derive_output_dir(
                os.path.dirname(video_path)))
            episode.errors.append(str(exc))
            episode.failed = 1
            run_result.episodes.append(episode)
            reporter.status('处理失败：%s' % exc)

    return run_result


def summarize(run_result):
    """把运行结果整理为可打印的报告行，供命令行与 GUI 复用。"""
    lines = ['台词智能截图 - 汇总', '=' * 46]
    for episode in run_result.episodes:
        lines.append('视频：%s' % os.path.basename(episode.video))
        lines.append('  输出目录：%s' % episode.output_dir)
        lines.append('  成功 %d 张（降级 %d 张）／跳过 %d 张／失败 %d 句／耗时 %.1f 秒'
                     % (episode.success, episode.degraded, episode.skipped,
                        episode.failed, episode.elapsed))
        lines.append('  完整性：台词 %d 句，缺图 %d 句，补跑 %d 张，样式基线 %s'
                     % (episode.total_cues, episode.integrity_missing,
                        episode.refilled, episode.style_source or '内置默认值'))
        covered = episode.success + episode.skipped + episode.refilled
        if episode.planned_cues < episode.total_cues:
            verdict = '抽样试跑（%d/%d 句）' % (episode.planned_cues, episode.total_cues)
        elif episode.failed == 0 and episode.integrity_missing == 0 and covered >= episode.total_cues:
            verdict = '通过' if episode.success else '无新增（均为已存在图片）'
        else:
            verdict = '需人工复核'
        lines.append('  眼睛状态：睁开 %d 张／半闭 %d 张／闭合 %d 张／无人像 %d 张'
                     % (episode.eye_open, episode.eye_half,
                        episode.eye_closed, episode.eye_unknown))
        if episode.eye_refine_tried:
            lines.append('  重采样换帧：触发 %d 句，成功换帧 %d 句'
                         % (episode.eye_refine_tried, episode.eye_refined))
        lines.append('  验收结论：%s（应出图 %d 张 / 台词 %d 句，失败 %d 句）'
                     % (verdict, covered, episode.total_cues, episode.failed))
        if episode.cleaned_images:
            lines.append('  重建清理：%d 张历史图片' % episode.cleaned_images)
    lines.append('')
    lines.append('合计：成功 %d 张，降级 %d 张，跳过 %d 张，失败 %d 句'
                 % (run_result.success, run_result.degraded,
                    run_result.skipped, run_result.failed))

    if run_result.incomplete_files:
        lines.append('')
        lines.append('未下载完成（已跳过，请下载完整后再处理）：')
        for path in run_result.incomplete_files:
            lines.append('  - %s' % ffmpeg_utils.describe_incomplete(path))

    if run_result.unreadable_files:
        lines.append('')
        lines.append('无法识别的文件（已跳过）：')
        for path in run_result.unreadable_files:
            lines.append('  - %s' % os.path.basename(path))

    return lines
