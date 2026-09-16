# -*- coding: utf-8 -*-
"""按 _audit.csv 的推荐批量删除剧情延续帧

使用场景：每集跑完并审计后，根据 audit 输出的「建议删除」/「复核」清单，把
不再保留的图片删掉，并把 _index.csv 中对应行的「文件名」列清空（保留台账
形态，重跑不会重补截）。

行为：
1. 读 <output>/_audit.csv，过滤 推荐 == recommend（默认 建议删除）
2. 对每条命中行：
   - 删除 <output>/<文件名>（文件存在时）
   - 在 _index.csv 中按「句序」定位该行，把「文件名」列清空
3. 写回 _index.csv（其它列原样保留）
4. 干跑模式（--dry-run）只打印计划，不动文件

与「重跑工具」的兼容性：台账行（文件名为空）会被 capture_story_frames 的
ledger 识别为「该空窗已处理」，重跑不会重补截。想重新补截某空窗时
删掉 _index.csv 中该行即可。
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(THIS_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from video_tool import config  # noqa: E402


def apply(output_dir: str, recommend: str = '建议删除',
          dry_run: bool = False) -> dict:
    """主入口：扫描 audit + 实际删图 + 写回 index。返回统计字典。"""
    audit_path = os.path.join(output_dir, '_audit.csv')
    index_path = os.path.join(output_dir, config.INDEX_CSV_NAME)
    if not os.path.isfile(audit_path):
        raise FileNotFoundError('找不到 audit 表：%s' % audit_path)
    if not os.path.isfile(index_path):
        raise FileNotFoundError('找不到索引表：%s' % index_path)

    with open(audit_path, 'r', newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        candidates = [row for row in reader if row.get('推荐') == recommend]
    if not candidates:
        return {'deleted': 0, 'tombstoned': 0, 'missing': 0, 'dry_run': dry_run}

    # 读索引
    with open(index_path, 'r', newline='', encoding='utf-8-sig') as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        rows = list(reader)
    if not header or '句序' not in header or '文件名' not in header:
        raise RuntimeError('索引表缺少必要列（句序 / 文件名）')

    seq_col = header.index('句序')
    img_col = header.index('文件名')

    # 索引按句序建 dict，方便定位
    row_by_seq: dict = {}
    for raw in rows:
        seq = raw[seq_col].strip() if len(raw) > seq_col else ''
        if seq:
            row_by_seq[seq] = raw

    deleted = tombstoned = missing = 0
    planned: list = []
    for cand in candidates:
        seq = cand.get('句序', '').strip()
        image = cand.get('文件名', '').strip()
        if not seq:
            continue
        if seq not in row_by_seq:
            missing += 1
            continue

        # 找图片路径
        image_path = os.path.join(output_dir, image) if image else ''
        plan_entry = {
            'seq': seq,
            'image': image,
            'image_exists': bool(image) and os.path.isfile(image_path),
            'index_has_image': bool(row_by_seq[seq][img_col].strip()),
        }
        planned.append(plan_entry)

        if dry_run:
            continue

        # 1) 删图
        if image and os.path.isfile(image_path):
            try:
                os.remove(image_path)
                deleted += 1
            except OSError as exc:
                print('  ! 删除失败 %s: %s' % (image, exc))

        # 2) 把索引行的 文件名 列清空
        if row_by_seq[seq][img_col].strip():
            row_by_seq[seq][img_col] = ''
            tombstoned += 1

    if not dry_run:
        # 写回索引（保留原始行顺序，不重新排序）
        with open(index_path, 'w', newline='', encoding='utf-8-sig') as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            for raw in rows:
                seq = raw[seq_col].strip() if len(raw) > seq_col else ''
                if seq and seq in row_by_seq:
                    writer.writerow(row_by_seq[seq])
                else:
                    writer.writerow(raw)

    return {
        'deleted': deleted,
        'tombstoned': tombstoned,
        'missing': missing,
        'planned': len(planned),
        'dry_run': dry_run,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='按 _audit.csv 推荐批量删除剧情延续帧（保留台账行）'
    )
    parser.add_argument('--input', required=True,
                        help='单集目录（…\\Love in the big City\\08）')
    parser.add_argument('--recommend', default='建议删除',
                        choices=['建议删除', '复核'],
                        help='只处理此推荐的行，默认「建议删除」')
    parser.add_argument('--dry-run', action='store_true',
                        help='只打印计划，不动文件')
    args = parser.parse_args(argv)

    video_dir = os.path.abspath(args.input)
    base = os.path.basename(video_dir.rstrip('\\/'))
    output_dir = os.path.join(video_dir, base + config.OUTPUT_SUFFIX)
    stats = apply(output_dir, recommend=args.recommend, dry_run=args.dry_run)
    if args.dry_run:
        print('干跑：计划删除 %d 张，tombstone %d 行（缺图 %d 张）'
              % (stats.get('planned', 0), stats.get('tombstoned', 0),
                 stats.get('missing', 0)))
    else:
        print('完成：删图 %d 张，索引 tombstone %d 行（缺图 %d 张）'
              % (stats['deleted'], stats['tombstoned'], stats['missing']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
