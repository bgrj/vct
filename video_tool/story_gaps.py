# -*- coding: utf-8 -*-
"""剧情延续帧：无台词空窗的枚举、选优与补截

台词截图只覆盖「有人在说话」的时段；台词之间的沉默画面（角色反应、物件
特写、回忆闪回等）同样承载剧情——例如前文台词提到的合照在后续空窗中被
镜头停留展示。此前这类画面依赖人工用 ffmpeg 逐段抽帧排查，既容易漏截也
容易在同一段里反复截到近重复的画面。

本模块把这套排查下沉为工具能力：
1. compute_gaps：从完整字幕时间轴枚举全部无台词空窗（片头、句间、片尾），
   一处不漏；
2. capture_story_frames：空窗内采样候选帧并按「剧情帧口径」评分选优——
   与台词帧不同，不施加闭眼/嘴部惩罚（哭戏闭眼、沉默反应都是合法剧情帧），
   只看清晰度、运动、构图与人像面积；
3. 同一空窗内按镜头分组去重，每个镜头只出一张代表帧，杜绝近重复；
4. 补截结果写入同一张 _index.csv，句序形如「<前接句序号 4 位><子后缀 a/b/c>」
   （如 0004a、0298b），让按句序排序时剧情帧天然插在「前一句台词图」与
   「后一句台词图」之间；起止时间码列记录所属空窗的范围；该行同时充当
   「此空窗已处理」的台账——图片被人工删除后行仍保留（文件名清空），重跑
   不会把人工淘汰过的空窗再补一遍。

语义判断（这张画面是否承载剧情）仍交给人工/AI 复核：报告列出全部空窗与
补截明细，复核时只需看现成的候选图决定去留，不必再手工抽帧找画面。
"""

import os
import shutil
from dataclasses import dataclass

from . import config, ffmpeg_utils, quality, renderer, subtitles


@dataclass
class StoryGap:
    """一段无台词的剧情空窗。"""

    start: float
    end: float
    kind: str                 # head（片头）/ gap（句间）/ tail（片尾）
    prev_cue: object = None   # 空窗前一句台词（SubtitleCue 或 None）
    next_cue: object = None   # 空窗后一句台词（SubtitleCue 或 None）

    @property
    def duration(self):
        """空窗时长（秒）。"""
        return max(0.0, self.end - self.start)

    @property
    def key(self):
        """空窗身份键：起止时间码（与索引表中的记录一致）。"""
        return '%s~%s' % (subtitles.format_timecode(self.start),
                          subtitles.format_timecode(self.end))


# 剧情帧的眼睛状态到索引表中文文案的映射（仅作复核参考，不参与选优）
_EYE_TEXT = {'open': '睁开', 'half': '半闭', 'closed': '闭合'}

_KIND_LABEL = {'head': '片头', 'gap': '句间', 'tail': '片尾'}


def make_story_seq(prev_cue_index, sub_letter):
    """把「前接台词序号 + 子后缀」拼成剧情帧 seq（如 prev=4, 'a' -> '0004a'）。

    没有前接台词（片头空窗）时传 None 或 0，固定写 0000<letter>，排序时落到最前。
    子后缀越界（>z）时抛 ValueError——超出 STORY_GAP_SUB_LETTERS 长度的请先把
    单字母方案扩展成 aa/ab/... 之类的双字母方案。
    """
    if sub_letter not in config.STORY_GAP_SUB_LETTERS:
        raise ValueError('子后缀 %r 超出字母表 %r'
                         % (sub_letter, config.STORY_GAP_SUB_LETTERS))
    prefix = 0 if prev_cue_index in (None, 0) else int(prev_cue_index)
    return '%04d%s' % (prefix, sub_letter)


# ---------------------------------------------------------------------------
# 空窗枚举
# ---------------------------------------------------------------------------
def compute_gaps(cues, duration, min_gap=None):
    """从完整字幕时间轴枚举全部无台词空窗。

    覆盖片头（0 -> 首句）、句间（上一句结束 -> 下一句开始）与片尾（末句
    结束 -> 视频结束），间隔达到 min_gap 才计入；返回按时间排序的
    StoryGap 列表。这一步把「找画面」从人工扫时间轴变成系统枚举，是防漏
    截的根本。
    """
    min_gap = config.STORY_GAP_MIN if min_gap is None else float(min_gap)
    gaps = []
    if not cues:
        return gaps

    if cues[0].start >= min_gap:
        gaps.append(StoryGap(0.0, cues[0].start, 'head', None, cues[0]))

    for prev_cue, next_cue in zip(cues, cues[1:]):
        span = next_cue.start - prev_cue.end
        if span >= min_gap:
            gaps.append(StoryGap(prev_cue.end, next_cue.start, 'gap',
                                 prev_cue, next_cue))

    if duration and duration - cues[-1].end >= min_gap:
        gaps.append(StoryGap(cues[-1].end, duration, 'tail', cues[-1], None))

    return gaps


def gap_text(gap):
    """空窗的索引描述：位置 + 前后台词摘录，供人工复核时判断剧情上下文。"""
    parts = []
    if gap.prev_cue is not None:
        parts.append('前接第 %d 句「%s」'
                     % (gap.prev_cue.index, _snippet(gap.prev_cue.text)))
    if gap.next_cue is not None:
        parts.append('后接第 %d 句「%s」'
                     % (gap.next_cue.index, _snippet(gap.next_cue.text)))
    if gap.kind == 'head':
        label = '片头无台词空窗'
    elif gap.kind == 'tail':
        label = '片尾无台词空窗'
    else:
        label = '句间无台词空窗'
    if parts:
        label += '（%s）' % '，'.join(parts)
    return label


def _snippet(text, limit=14):
    """台词摘录：取首行前若干字，供索引表里快速辨认上下文。"""
    line = str(text or '').split('\n')[0].strip()
    if len(line) > limit:
        return line[:limit] + '…'
    return line


# ---------------------------------------------------------------------------
# 选优与补截
# ---------------------------------------------------------------------------
def _story_rank(score):
    """剧情帧口径的综合分。

    与台词帧的 score_candidates 不同：不看睁眼/嘴部状态——哭戏闭眼、沉默
    反应是合法的剧情画面，用台词帧的闭眼重罚会把它们全部压下去。这里只
    保留客观画质（清晰度、运动）与画面价值（构图居中、人像面积）。
    """
    detail = score.detail or {}
    area = min(1.0, float(detail.get('area_ratio', 0.0)) / config.FACE_AREA_REFERENCE)
    return (
        config.WEIGHT_SHARPNESS * score.sharpness_norm
        - config.WEIGHT_MOTION * score.motion_penalty
        + config.WEIGHT_COMPOSITION * score.composition_score
        + config.WEIGHT_FACE * area
    )


def _story_row(gap, score, rank, image_name, seq):
    """把一张剧情延续帧整理成索引行（列与 renderer.INDEX_HEADER 对齐）。"""
    return {
        'index': seq,
        'start_timecode': subtitles.format_timecode(gap.start),
        'end_timecode': subtitles.format_timecode(gap.end),
        'duration': round(gap.duration, 3),
        'text': gap_text(gap),
        'image': image_name,
        'frame_timecode': renderer.format_timecode(score.timestamp),
        'total': round(rank, 4),
        'sharpness': round(score.sharpness, 1),
        'motion_penalty': score.motion_penalty,
        'face_count': score.face_count,
        'eye_state_text': _EYE_TEXT.get(score.eye_state or '', ''),
        'mouth_natural': '',
        'speaking': '',
        'composition': score.composition_score,
        'timing': score.timing_score,
        'shot': score.shot_id,
        'frame_type': '剧情延续帧',
        'degraded': False,
    }


def _capture_one_gap(video_path, gap, analyzer, work_dir, output_dir, episode_tag,
                     prev_cue_index, max_per_gap):
    """补截单个空窗，返回 (索引行列表, 状态说明；补截成功时为 None)。

    命名口径：seq = `<前接句序号 4 位><子后缀 a/b/c...>`，让空窗内补出的几张
    按时间顺序得到 a、b、c 这样的连续子后缀。无前接台词（片头空窗）时
    prev_cue_index 传 None，由 make_story_seq 退化为 0000a/b/c。
    """
    window_start = gap.start + config.STORY_GAP_EDGE_MARGIN
    window_end = gap.end - config.STORY_GAP_EDGE_MARGIN
    window = window_end - window_start
    if window < config.STORY_GAP_MIN_WINDOW:
        return [], '空窗过短（扣除台词边距后仅 %.1f 秒），不补截' % max(0.0, window)

    # 长片头等大空窗按上限均匀采样，避免逐秒采样拖慢处理
    count = max(1, min(config.STORY_GAP_MAX_SAMPLES,
                       int(round(window * config.STORY_GAP_FPS))))
    fps = count / window

    # 清掉上一个空窗的候选帧，避免混入本次打分
    for name in os.listdir(work_dir):
        if name.startswith('story_'):
            try:
                os.remove(os.path.join(work_dir, name))
            except OSError:
                pass

    pattern = os.path.join(work_dir, 'story_%03d.jpg')
    files = ffmpeg_utils.sample_frames(video_path, window_start, window, fps, pattern)
    if not files:
        return [], '未能抽取到候选帧'

    candidates = []
    for position, path in enumerate(files):
        timestamp = window_start + position / fps
        timestamp = min(max(timestamp, window_start), window_end)
        candidates.append((path, timestamp))

    scores = quality.score_candidates(candidates, analyzer)
    if not scores:
        return [], '候选帧全部读取失败'

    usable = [item for item in scores if not item.flat]
    if not usable:
        return [], '空窗内均为黑场/字幕卡等平坦画面，不补截'

    # 镜头分组去重：同一镜头只保留得分最高的一帧，杜绝近重复图片
    by_shot = {}
    for score in usable:
        rank = _story_rank(score)
        current = by_shot.get(score.shot_id)
        if current is None or rank > current[0]:
            by_shot[score.shot_id] = (rank, score)

    # 按时间先后排序后取前几张，保证同一空窗内的剧情帧遵循叙事顺序
    chosen = sorted(
        (item[1] for item in by_shot.values()),
        key=lambda score: score.timestamp,
    )[:max_per_gap]

    rows = []
    for position, score in enumerate(chosen):
        sub_letter = config.STORY_GAP_SUB_LETTERS[position]
        seq = make_story_seq(prev_cue_index, sub_letter)
        image_name = renderer.build_story_image_name(episode_tag, seq, score.timestamp)
        shutil.copyfile(score.path, os.path.join(output_dir, image_name))
        rows.append(_story_row(gap, score, _story_rank(score), image_name, seq))
    return rows, None


def capture_story_frames(video_path, gaps, analyzer, work_dir, output_dir, episode_tag,
                         index_path, max_per_gap=None, reporter=None):
    """逐空窗补截剧情延续帧，返回 (索引行列表, 空窗明细, 统计字典)。

    断点续跑与防重复补截：索引表中已有对应空窗（按起止时间码识别）的记录
    时跳过该空窗——无论图片还在（上次已补截）还是已被人工删除（人工淘汰，
    行保留为台账、文件名清空）。因此复核时删除不要的图片后直接重跑即可，
    不会把淘汰过的画面再补一遍；想重新补截某个空窗，删掉索引表中那一行
    再重跑即可。
    """
    max_per_gap = config.STORY_GAP_MAX_PER_GAP if max_per_gap is None else int(max_per_gap)

    ledger = {}
    for record in renderer.read_story_index_rows(index_path):
        ledger[record['key']] = record

    rows, notes = [], []
    captured = skipped = 0
    for gap in gaps:
        if reporter is not None and reporter.cancelled():
            notes.append((gap, '已取消，该空窗及其后的空窗未补截'))
            break

        if gap.key in ledger:
            skipped += 1
            image = ledger[gap.key].get('image') or ''
            if image:
                notes.append((gap, '已补截过，跳过（断点续跑）'))
            else:
                notes.append((gap, '已人工淘汰，不再重复补截'))
            continue

        try:
            gap_rows, reason = _capture_one_gap(
                video_path, gap, analyzer, work_dir, output_dir, episode_tag,
                gap.prev_cue.index if gap.prev_cue is not None else None,
                max_per_gap,
            )
        except Exception as exc:                      # 兜底，绝不中断整集
            notes.append((gap, '补截失败：%s' % exc))
            continue

        if reason:
            notes.append((gap, reason))
            continue

        rows.extend(gap_rows)
        captured += len(gap_rows)
        notes.append((gap, '补截 %d 张：%s'
                      % (len(gap_rows), ' '.join(row['image'] for row in gap_rows))))
        if reporter is not None:
            reporter.status('剧情延续帧：%s ~ %s -> %s'
                            % (subtitles.format_timecode(gap.start),
                               subtitles.format_timecode(gap.end),
                               ' '.join(row['image'] for row in gap_rows)))

    stats = {'gaps': len(gaps), 'captured': captured, 'skipped': skipped}
    return rows, notes, stats
