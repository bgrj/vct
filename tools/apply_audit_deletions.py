# -*- coding: utf-8 -*-
"""Apply _audit.csv recommendations: delete story-gap images and tombstone index rows.

Reads <output>/_audit.csv, filters recommendation == recommend (default: delete),
deletes matching images, and clears the filename column in _index.csv (ledger kept).
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


def apply(output_dir: str, recommend: str = None, dry_run: bool = False) -> dict:
    """Delete images and tombstone index rows for the given recommendation."""
    if recommend is None:
        recommend = config.RECOMMEND_DELETE
    audit_path = os.path.join(output_dir, config.AUDIT_CSV_NAME)
    index_path = os.path.join(output_dir, config.INDEX_CSV_NAME)
    if not os.path.isfile(audit_path):
        raise FileNotFoundError('audit CSV not found: %s' % audit_path)
    if not os.path.isfile(index_path):
        raise FileNotFoundError('index CSV not found: %s' % index_path)

    with open(audit_path, 'r', newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        # Accept English or legacy Chinese recommendation values
        legacy = {
            config.RECOMMEND_DELETE: ('delete', '建议删除'),
            config.RECOMMEND_REVIEW: ('review', '复核'),
        }
        accept = set(legacy.get(recommend, (recommend,)))
        accept.add(recommend)
        candidates = [
            row for row in reader
            if (row.get('recommendation') or row.get('推荐') or '').strip() in accept
        ]
    if not candidates:
        return {'deleted': 0, 'tombstoned': 0, 'missing': 0, 'dry_run': dry_run}

    with open(index_path, 'r', newline='', encoding='utf-8-sig') as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        rows = list(reader)
    if not header:
        raise RuntimeError('index CSV has no header')

    # English preferred; fall back to legacy Chinese column names
    def col(english, chinese):
        if english in header:
            return header.index(english)
        if chinese in header:
            return header.index(chinese)
        raise RuntimeError('index missing column %s / %s' % (english, chinese))

    seq_col = col('seq', '句序')
    img_col = col('filename', '文件名')

    row_by_seq = {}
    for raw in rows:
        seq = raw[seq_col].strip() if len(raw) > seq_col else ''
        if seq:
            row_by_seq[seq] = raw

    deleted = tombstoned = missing = 0
    planned = []
    for cand in candidates:
        seq = (cand.get('seq') or cand.get('句序') or '').strip()
        image = (cand.get('filename') or cand.get('文件名') or '').strip()
        if not seq:
            continue
        if seq not in row_by_seq:
            missing += 1
            continue

        image_path = os.path.join(output_dir, image) if image else ''
        planned.append({
            'seq': seq,
            'image': image,
            'image_exists': bool(image) and os.path.isfile(image_path),
        })
        if dry_run:
            continue

        if image and os.path.isfile(image_path):
            try:
                os.remove(image_path)
                deleted += 1
            except OSError as exc:
                print('  ! delete failed %s: %s' % (image, exc))

        if row_by_seq[seq][img_col].strip():
            row_by_seq[seq][img_col] = ''
            tombstoned += 1

    if not dry_run:
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
        description='Apply audit recommendations: delete story frames, tombstone index'
    )
    parser.add_argument('--input', required=True,
                        help='Episode folder (e.g. ...\\your-show\\08)')
    parser.add_argument('--recommend', default=config.RECOMMEND_DELETE,
                        choices=[config.RECOMMEND_DELETE, config.RECOMMEND_REVIEW],
                        help='Which recommendation to apply (default: delete)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print plan only; do not modify files')
    args = parser.parse_args(argv)

    video_dir = os.path.abspath(args.input)
    base = os.path.basename(video_dir.rstrip('\\/'))
    output_dir = os.path.join(video_dir, base + config.OUTPUT_SUFFIX)
    stats = apply(output_dir, recommend=args.recommend, dry_run=args.dry_run)
    if args.dry_run:
        print('dry-run: plan delete %d, tombstone %d, missing %d'
              % (stats.get('planned', 0), stats.get('tombstoned', 0),
                 stats.get('missing', 0)))
    else:
        print('done: deleted %d, tombstoned %d, missing %d'
              % (stats['deleted'], stats['tombstoned'], stats['missing']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
