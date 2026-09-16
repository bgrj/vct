# -*- coding: utf-8 -*-
"""把旧版「X##」剧情延续帧迁移到新命名格式

旧版本：剧情帧用独立序号 `X01/X02/...` 命名，自然与台词帧 `0001..0298` 分块
排序（_index.csv 中 X 行集中在末尾）。

新版本：剧情帧用「前接句序号 4 位 + 子后缀 a/b/c」命名（如 `0004a`），让
按句序排序时剧情帧天然插在「前一句台词图」与「后一句台词图」之间。

用法：
    python tools/migrate_story_naming.py --input "D:\\...\\Love in the big City\\08"
    python tools/migrate_story_naming.py --input "D:\\...\\Love in the big City\\08" --dry-run

行为：
1. 读取 `<input>/<input_basename>-pc/_index.csv`，把行拆成「台词行」与「旧 X 行」
2. 对每条旧 X 行：
   - 在台词行中找「结束时间码 ≤ gap.start」的最后一行的 index 作为 prev_cue
   - 片头空窗（无 prev_cue）→ seq = `0000a/b/c`
   - 同空窗（按 start_tc~end_tc 分组）内的旧 X 行按原 X## 顺序赋 a/b/c
3. 重命名图片文件：`S01E08_X01_00-00-23.224.jpg` → `S01E08_0004a_00-00-23.224.jpg`
4. 更新索引行「句序」「文件名」两列
5. 用 renderer 的排序键重排整张表，写回 CSV

约束与提示：
- 仅迁移「句序以 X 开头」的旧剧情帧行；不动台词行
- 如果某条 X 行指向的图片已不存在，仍按规则生成新 seq 与空文件名（台账行保留）
- 重命名采用「先建立新文件再删旧文件」的策略：失败时旧文件不会丢失
- 旧 X 行中若检测到 `prev_cue` 同时也是「剧情延续帧」（prev_cue 字段可能为空），
  fallback 用整段字幕 cue 顺序的台词 index
- 干跑模式（--dry-run）只打印计划，不动文件
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from typing import Dict, List, Optional, Tuple

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(THIS_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from video_tool import config, renderer  # noqa: E402


_OLD_STORY_INDEX_RE = None  # lazy compile below


def _old_story_index_re():
    global _OLD_STORY_INDEX_RE
    if _OLD_STORY_INDEX_RE is None:
        import re
        _OLD_STORY_INDEX_RE = re.compile(r'^X\d+$')
    return _OLD_STORY_INDEX_RE


def _load_index_rows(index_path: str) -> Tuple[List[str], List[dict]]:
    """读回索引表所有行：表头 + 行字典列表（保留原始列顺序用于写回）。"""
    with open(index_path, 'r', newline='', encoding='utf-8-sig') as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if not header:
            return [], []
        rows = []
        for raw in reader:
            if not raw or not raw[0].strip():
                continue
            rows.append({col: (raw[idx] if idx < len(raw) else '')
                         for idx, col in enumerate(header)})
    return header, rows


def _parse_timecode(value: str) -> Optional[float]:
    """把 'HH:MM:SS.mmm' 解析为秒；解析失败返回 None。"""
    if not value:
        return None
    try:
        h_part, rest = value.split(':', 1)
        m_part, rest = rest.split(':', 1)
        return int(h_part) * 3600 + int(m_part) * 60 + float(rest)
    except (ValueError, AttributeError):
        return None


def _tc_to_seconds(timecode: str) -> float:
    """与字幕里的 timecode 解析一致（HH:MM:SS.mmm 形式）。"""
    seconds = _parse_timecode(timecode)
    return seconds if seconds is not None else 0.0


def _build_sub_letter_map(per_gap_x_rows: List[dict]) -> Dict[str, str]:
    """同一空窗内的旧 X## 行按原 X 序号顺序映射到 a/b/c... 子后缀。"""
    sorted_rows = sorted(per_gap_x_rows,
                         key=lambda row: int(row['句序'].lstrip('X')))
    mapping: Dict[str, str] = {}
    for position, row in enumerate(sorted_rows):
        if position >= len(config.STORY_GAP_SUB_LETTERS):
            raise RuntimeError(
                '同空窗内旧剧情帧超过 %d 张，字母表不够；请先调小 STORY_GAP_MAX_PER_GAP'
                ' 或扩展双字母方案。空窗起点=%s 终点=%s'
                % (len(config.STORY_GAP_SUB_LETTERS),
                   row.get('起始时间码', ''), row.get('结束时间码', ''))
            )
        mapping[row['句序']] = config.STORY_GAP_SUB_LETTERS[position]
    return mapping


def _new_image_name(old_name: str, new_seq: str) -> str:
    """从旧文件名 `S01E08_X01_00-00-23.224.jpg` 生成新名 `S01E08_0004a_00-00-23.224.jpg`。
    时间码段保持不变（仍源自视频时间轴），只换序号段。
    """
    stem, ext = os.path.splitext(old_name)
    parts = stem.rsplit('_', 1)
    if len(parts) != 2:
        raise ValueError('无法解析旧文件名：%s' % old_name)
    episode_tag = parts[0]
    return '%s_%s_%s%s' % (episode_tag.rsplit('_', 1)[0], new_seq,
                           parts[1], ext)


def _new_image_name_from_episode(episode_tag: str, old_name: str,
                                 new_seq: str) -> str:
    """用单独的 episode_tag 拼新名（防止旧名里的 episode_tag 被破坏）。"""
    stem, ext = os.path.splitext(old_name)
    parts = stem.rsplit('_', 1)
    if len(parts) != 2:
        raise ValueError('无法解析旧文件名：%s' % old_name)
    return '%s_%s_%s%s' % (episode_tag, new_seq, parts[1], ext)


def migrate(input_dir: str, dry_run: bool = False) -> dict:
    """主入口：扫描索引表 + 实际改名 + 写回 CSV。返回统计字典。"""
    video_dir = os.path.abspath(input_dir)
    base = os.path.basename(video_dir.rstrip('\\/'))
    output_dir = os.path.join(video_dir, base + config.OUTPUT_SUFFIX)
    index_path = os.path.join(output_dir, config.INDEX_CSV_NAME)
    if not os.path.isfile(index_path):
        raise FileNotFoundError('找不到索引表：%s' % index_path)

    header, rows = _load_index_rows(index_path)
    if '句序' not in header or '文件名' not in header \
            or '起始时间码' not in header or '结束时间码' not in header:
        raise RuntimeError('索引表缺少必要列（句序 / 文件名 / 起始时间码 / 结束时间码）')

    # 拆台词行与旧 X 行
    old_x_re = _old_story_index_re()
    cue_rows: List[dict] = []
    old_x_rows: List[dict] = []
    for row in rows:
        idx = row.get('句序', '').strip()
        if old_x_re.match(idx):
            old_x_rows.append(row)
        else:
            cue_rows.append(row)
    if not old_x_rows:
        print('未发现旧 X## 行，无需迁移。')
        return {'renamed': 0, 'skipped': 0, 'dry_run': dry_run}

    # 准备台词行的时间索引：按结束时间码升序排列
    cue_rows_by_end: List[Tuple[float, int]] = []
    for row in cue_rows:
        end_sec = _tc_to_seconds(row.get('结束时间码', ''))
        try:
            idx_int = int(row.get('句序', ''))
        except ValueError:
            idx_int = 0
        cue_rows_by_end.append((end_sec, idx_int))
    cue_rows_by_end.sort()

    def find_prev_cue_index(gap_start_sec: float) -> Optional[int]:
        """从结束时间码 ≤ gap.start_sec 的台词行中选 index 最大者；没有则 None（片头）。"""
        best = None
        for end_sec, idx_int in cue_rows_by_end:
            if end_sec <= gap_start_sec + 1e-3:
                if best is None or idx_int > best:
                    best = idx_int
            else:
                break
        return best

    # 同空窗内的旧 X 行分组（key = start_tc~end_tc）
    gaps: Dict[str, List[dict]] = {}
    for row in old_x_rows:
        key = '%s~%s' % (row.get('起始时间码', ''), row.get('结束时间码', ''))
        gaps.setdefault(key, []).append(row)

    # 生成新计划
    plan: List[dict] = []  # 每条 = {old_row, new_seq, new_image_name, exists}
    episode_tag: Optional[str] = None
    for key, group in gaps.items():
        sub_map = _build_sub_letter_map(group)
        for old_row in group:
            start_tc = old_row.get('起始时间码', '')
            end_tc = old_row.get('结束时间码', '')
            start_sec = _tc_to_seconds(start_tc)
            prev_idx = find_prev_cue_index(start_sec)
            sub = sub_map[old_row['句序']]
            new_seq = renderer_make_story_seq(prev_idx, sub)
            old_image = old_row.get('文件名', '')
            if old_image:
                episode_tag = episode_tag or old_image.split('_', 1)[0]
                new_image = _new_image_name_from_episode(
                    episode_tag, old_image, new_seq
                )
            else:
                new_image = ''
            plan.append({
                'old_row': old_row,
                'old_index': old_row['句序'],
                'new_seq': new_seq,
                'old_image': old_image,
                'new_image': new_image,
                'prev_cue': prev_idx,
                'gap_key': key,
            })

    # 统计 & 干跑预览
    print('=' * 60)
    print('迁移计划：%d 个空窗，共 %d 张旧 X 帧' % (len(gaps), len(plan)))
    print('-' * 60)
    show_count = min(15, len(plan))
    for item in plan[:show_count]:
        print('  %-6s -> %-6s  (前接 #%s)  %s'
              % (item['old_index'], item['new_seq'],
                 item['prev_cue'] if item['prev_cue'] is not None else '片头',
                 item['new_image']))
    if len(plan) > show_count:
        print('  ...（其余 %d 条略）' % (len(plan) - show_count))
    print('=' * 60)

    if dry_run:
        print('干跑模式：未实际修改任何文件。')
        return {'renamed': 0, 'skipped': 0, 'planned': len(plan), 'dry_run': True}

    # 实际改名：先建新名，再删旧名（出错时旧文件仍在）
    renamed = skipped = 0
    new_paths_to_remove: List[str] = []
    for item in plan:
        if not item['old_image']:
            # 旧台账行（图片早被人工删了）：只更新句序与文件名字段
            item['old_row']['句序'] = item['new_seq']
            item['old_row']['文件名'] = ''
            skipped += 1
            continue
        old_path = os.path.join(output_dir, item['old_image'])
        new_path = os.path.join(output_dir, item['new_image'])
        if not os.path.isfile(old_path):
            # 图片已不在（人工删过但行还留着）：保持台账行格式
            item['old_row']['句序'] = item['new_seq']
            item['old_row']['文件名'] = ''
            skipped += 1
            continue
        if os.path.isfile(new_path):
            raise RuntimeError('目标文件已存在，无法安全改名：%s' % new_path)
        os.rename(old_path, new_path)
        item['old_row']['句序'] = item['new_seq']
        item['old_row']['文件名'] = item['new_image']
        renamed += 1
        new_paths_to_remove.append(old_path)  # 实际 rename 已删

    # 写回 CSV（按 renderer._index_sort_key 重排）
    merged = {}
    for row in rows:
        # row 字典视图里 plan 已经把旧 X 行的 句序/文件名 改写成新值
        merged[row['句序']] = [row.get(col, '') for col in header]
    sorted_keys = sorted(merged.keys(), key=renderer._index_sort_key)
    with open(index_path, 'w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for key in sorted_keys:
            writer.writerow(merged[key])

    print('完成：改名 %d 张，保留台账行 %d 条，索引表已重排并写回。'
          % (renamed, skipped))
    return {'renamed': renamed, 'skipped': skipped, 'dry_run': False}


def renderer_make_story_seq(prev_cue_index, sub_letter):
    """透传 story_gaps.make_story_seq，避免直接 import story_gaps（其依赖较重）。"""
    from video_tool import story_gaps
    return story_gaps.make_story_seq(prev_cue_index, sub_letter)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='把旧版 X## 剧情延续帧迁移到新命名格式（前接句序号 4 位 + 子后缀）'
    )
    parser.add_argument('--input', required=True,
                        help='单集目录（…\\Love in the big City\\08）')
    parser.add_argument('--dry-run', action='store_true',
                        help='只打印迁移计划，不动文件')
    args = parser.parse_args(argv)
    try:
        migrate(args.input, dry_run=args.dry_run)
    except Exception as exc:
        print('迁移失败：%s' % exc)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
