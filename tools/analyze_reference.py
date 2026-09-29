# -*- coding: utf-8 -*-
"""参考素材样式分析入口（命令行，可独立运行）

扫描参考目录中的人工截图，统计字幕字号、描边宽度、行距与贴底位置，
产出项目根目录下的 style_profile.json，供台词渲染与评分消费。

用法（在项目目录下执行）：
    python tools/analyze_reference.py --reference <目录>    # 必填：参考素材根目录
    python tools/analyze_reference.py --reference <目录> --group 素材组A
    python tools/analyze_reference.py --reference <目录> --limit 100
    python tools/analyze_reference.py --reference <目录> --print-only
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from video_tool import config, style_profile            # noqa: E402

# 未指定 --group 时，优先选用该组名作为样式基线（若目录中不存在则回退到第一组）
STYLE_BASELINE_GROUP = 'reference'


def collect_groups(reference_dir, group=None):
    """返回 {组名: [图片路径, ...]}。"""
    if not os.path.isdir(reference_dir):
        raise SystemExit('素材目录不存在：%s' % reference_dir)

    names = [group] if group else sorted(
        name for name in os.listdir(reference_dir)
        if os.path.isdir(os.path.join(reference_dir, name))
    )
    if not names:
        names = ['']

    groups = {}
    for name in names:
        root = os.path.join(reference_dir, name) if name else reference_dir
        images = style_profile.iter_reference_images(root)
        if images:
            groups[name or os.path.basename(reference_dir)] = images
    if not groups:
        raise SystemExit('素材目录中没有找到图片：%s' % reference_dir)
    return groups


def print_summary(group, stats):
    """打印单组素材的统计摘要。"""
    medians = stats.get('medians') or {}
    print('  %s：命中 %d 张（跳过 %d 张无字幕/无法读取的图）'
          % (group, stats['samples'], stats['skipped']))
    if not stats['samples']:
        return
    print('    字号/画面高度 : %.4f' % medians.get('font_size_ratio', 0.0))
    print('    描边/字号     : %.4f' % medians.get('stroke_ratio', 0.0))
    print('    行距倍数      : %.3f' % (medians.get('line_spacing') or 0.0))
    print('    贴底距离/高度 : %.4f' % medians.get('bottom_margin_ratio', 0.0))
    print('    文本水平偏移  : %+.4f（接近 0 表示居中）' % medians.get('center_offset_ratio', 0.0))
    print('    文本宽度/画宽 : %.4f' % medians.get('text_width_ratio', 0.0))
    print('    行数 中位/90分位 : %.1f / %.1f'
          % (medians.get('line_count_median', 0.0), medians.get('line_count_p90', 0.0)))


def main():
    parser = argparse.ArgumentParser(description='统计参考素材的字幕样式并写出样式基线文件')
    parser.add_argument('--reference', required=True,
                        help='reference image root (required), e.g. ...\\your-show\\reference')
    parser.add_argument('--group', default='', help='only scan this subdirectory')
    parser.add_argument('--limit', type=int, default=0, help='max images per group; 0 = all')
    parser.add_argument('--print-only', action='store_true', help='print only; do not write profile')
    args = parser.parse_args()

    started = time.time()
    print('参考素材目录：%s' % args.reference)
    groups = collect_groups(args.reference, args.group or None)

    print('开始统计（每组最多 %s 张）…' % (args.limit or '全部'))
    all_stats = {}
    for name, images in groups.items():
        print('\n[%s] 共 %d 张' % (name, len(images)))
        stats = style_profile.analyze_reference_images(
            images, limit=args.limit, reporter=lambda text: print(text)
        )
        print_summary(name, stats)
        all_stats[name] = stats

    baseline_name = args.group or STYLE_BASELINE_GROUP
    if baseline_name not in all_stats:
        baseline_name = sorted(all_stats)[0]
    baseline_stats = all_stats[baseline_name]
    if not baseline_stats['samples']:
        raise SystemExit('字幕样式基准「%s」未统计到有效样本，无法生成样式基线' % baseline_name)

    source = '%s（%d 张命中样本，全量素材 %d 组）' % (
        baseline_name, baseline_stats['samples'], len(all_stats)
    )
    style = style_profile.build_style_from_stats(baseline_stats, source)

    print('\n样式基线（来源：%s）' % source)
    print('  字体        : %s' % style.font_path)
    print('  字号比例    : %.4f' % style.font_size_ratio)
    print('  描边比例    : %.4f' % style.stroke_ratio)
    print('  行距倍数    : %.3f' % style.line_spacing)
    print('  贴底比例    : %.4f' % style.bottom_margin_ratio)
    print('  最大行数    : %d' % style.max_lines)
    print('  字色/描边色 : %s / %s' % (style.text_color, style.stroke_color))

    if args.print_only:
        print('\n已按 --print-only 要求跳过写文件。')
    else:
        path = style_profile.save_style_profile(style, all_stats)
        print('\n样式基线已写入：%s' % path)

    print('耗时：%.1f 秒' % (time.time() - started))
    return 0


if __name__ == '__main__':
    sys.exit(main())
