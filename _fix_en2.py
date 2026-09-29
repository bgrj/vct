# -*- coding: utf-8 -*-
from pathlib import Path
import re
import py_compile

p = Path(r'd:\03_Knowledge\intimate\video-pics-cut\video_tool\pipeline.py')
t = p.read_text(encoding='utf-8')

# Fix missing paren if present
t = t.replace(
    "reporter.status('Report written: %s' % path\n\n\n",
    "reporter.status('Report written: %s' % path)\n\n\n",
)
t = t.replace(
    "reporter.status('Report written: %s' % path\n\n",
    "reporter.status('Report written: %s' % path)\n\n",
)

pairs = [
    ("raise VideoToolError('未能抽取到候选帧')",
     "raise VideoToolError('failed to extract candidate frames')"),
    ("raise VideoToolError('候选帧全部读取失败')",
     "raise VideoToolError('all candidate frames failed to load')"),
    ("raise VideoToolError('无法读取视频轨道信息')",
     "raise VideoToolError('cannot read video stream info')"),
    ("raise VideoToolError('字幕轨解析后没有任何台词')",
     "raise VideoToolError('subtitle track has no cues after parse')"),
    ("raise VideoToolError('指定的句序范围没有台词（共 %d 句）' % len(cues))",
     "raise VideoToolError('no cues in selected range (total %d)' % len(cues))"),
    ("raise VideoToolError('输入目录不存在：%s' % input_dir)",
     "raise VideoToolError('input directory not found: %s' % input_dir)"),
    ("reporter.status('跳过未下载完成的文件：%s' % file_name)",
     "reporter.status('skip incomplete download: %s' % file_name)"),
    ("reporter.status('已取消，剩余缺失台词未补跑')",
     "reporter.status('cancelled; remaining missing cues not refilled')"),
    ("reporter.status('%s 完成：%s' % (prefix, row['image']))",
     "reporter.status('%s done: %s' % (prefix, row['image']))"),
    ("reporter.status('%s 失败：%s' % (prefix, exc))",
     "reporter.status('%s failed: %s' % (prefix, exc))"),
    ("reporter.status('%s 出现未预期错误：%s' % (prefix, exc))",
     "reporter.status('%s unexpected error: %s' % (prefix, exc))"),
    ("reporter.status('已取消，剩余闭眼句未重采样')",
     "reporter.status('cancelled; remaining eye-refine cues skipped')"),
    ("reporter.status('%s 无改善，保留原图' % prefix)",
     "reporter.status('%s no improvement; keep original' % prefix)"),
    ("reporter.status('正在读取轨道信息：%s' % os.path.basename(video_path))",
     "reporter.status('reading streams: %s' % os.path.basename(video_path))"),
    ("reporter.status('正在导出中文字幕轨…')",
     "reporter.status('exporting subtitle track...')"),
    ("reporter.status('人像分析：%s' % result.face_backend)",
     "reporter.status('face backend: %s' % result.face_backend)"),
    ("reporter.status('台词样式基线：%s' % result.style_source)",
     "reporter.status('style profile: %s' % result.style_source)"),
    ("reporter.status('重建模式：已清空 %d 张历史图片与旧索引' % result.cleaned_images)",
     "reporter.status('clean rebuild removed %d images (+ index)' % result.cleaned_images)"),
    ("reporter.status('已取消，剩余台词未处理')",
     "reporter.status('cancelled; remaining cues not processed')"),
    ("reporter.status('%s 已存在，跳过（断点续跑）' % prefix)",
     "reporter.status('%s exists, skip (resume)' % prefix)"),
    ("reporter.status('%s 已处理，预计剩余 %s' % (prefix, eta))",
     "reporter.status('%s done, ETA %s' % (prefix, eta))"),
    ("reporter.status('发现 %d 句缺少图片，开始补跑…' % len(missing))",
     "reporter.status('%d cues missing images; refilling...' % len(missing))"),
    ("reporter.status('正在扫描目录：%s' % input_dir)",
     "reporter.status('scanning: %s' % input_dir)"),
    ("reporter.status('未找到可处理的视频文件')",
     "reporter.status('no processable video files found')"),
    ("reporter.status('已取消')",
     "reporter.status('cancelled')"),
    ('"""按「视频所在目录名 + -pc」推导输出目录。',
     '"""Derive output dir = episode folder basename + -pics.'),
    ('# 也会让 -pc 目录在浏览器里出现几百张一闪而过的图片。',
     '# Avoid flooding the -pics folder with temporary frames.'),
    ("'''处理输入目录中的全部视频，每个视频输出到其所在目录的 <目录名>-pc 下。'''",
     '"""Process all videos under input_dir; each writes to <basename>-pics."""'),
    ('"""处理输入目录中的全部视频，每个视频输出到其所在目录的 <目录名>-pc 下。"""',
     '"""Process all videos under input_dir; each writes to <basename>-pics."""'),
    ("reporter.status('补跑完成：补出 %d 张，仍缺 %d 句'",
     "reporter.status('refill done: +%d images, still missing %d'"),
    ("'闭眼重采样：%d 句触发，%d 句换成更好的眼睛状态'",
     "'eye refine: %d triggered, %d improved'"),
]

n = 0
for a, b in pairs:
    if a in t:
        t = t.replace(a, b)
        n += 1
    else:
        print('miss:', a[:70])

t = re.sub(
    r"reporter\.status\('%s 已更换：%s（眼睛 %s，得分 %.2f）'",
    "reporter.status('%s replaced: %s (eye %s, score %.2f)'",
    t,
)

# Batch-run section header
t = t.replace('# 整目录处理', '# Batch run')

p.write_text(t, encoding='utf-8')
print('replaced', n)
py_compile.compile(str(p), doraise=True)
print('syntax ok')

# story_gaps snippet docstring
sg = Path(r'd:\03_Knowledge\intimate\video-pics-cut\video_tool\story_gaps.py')
s = sg.read_text(encoding='utf-8')
s = s.replace(
    '"""台词摘录：取首行前若干字，供索引表里快速辨认上下文。"""',
    '"""Short first-line snippet for index context."""',
)
s = s.replace("return line[:limit] + '…'", "return line[:limit] + '...'")
s = s.replace("return [], '候选帧全部读取失败'", "return [], 'all candidate frames failed to load'")
s = s.replace("notes.append((gap, '补截失败：%s' % exc))", "notes.append((gap, 'capture failed: %s' % exc))")
sg.write_text(s, encoding='utf-8')
print('story_gaps ok')
