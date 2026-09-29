# 通用剧集目录模板（`your-show`）

本目录**不含真实视频**，只说明应如何摆放素材。请把真实剧集放在工具包**外侧**、与 `video-pics-cut` **并列**的文件夹中（名称任意，不必叫 `your-show`）。

## 推荐布局

```text
<工作区>/
├── video-pics-cut/            ← 工具（本仓库）
└── your-show/                 ← 你的剧名（可任意）
    ├── 01/
    │   ├── Show.Name.S01E01.mkv
    │   └── 01-pics/           ← 运行后自动生成
    ├── 02/
    │   ├── Show.Name.S01E02.mkv
    │   └── 02-pics/
    └── reference/             ← 可选：人工截好的参考图（重算字幕样式时用）
        └── （若干 .jpg）
```

## 规则摘要

| 项 | 约定 |
| --- | --- |
| 单集文件夹 | 两位数字 `01`、`02`…（输出目录为 `01-pics`） |
| 视频文件名 | 须含 `SxxExx`（如 `S01E01`），否则图片前缀用视频主名 |
| 字幕 | 需要**文字型软字幕**（非硬字幕、非 PGS） |
| 输出 | 同集目录内 `{集文件夹名}-pics\`：`*.jpg` + `_index.csv` + `_report.txt` |

## 怎么跑

1. 按上面布局放好一集视频  
2. 打开 `video-pics-cut`，双击 `scripts\run_gui.bat`，浏览选中 `your-show\01`  
3. 或：`python main.py --input "..\your-show\01" --max-cues 3` 先试跑  

详见仓库根目录 [QUICKSTART.md](../../QUICKSTART.md) 与 [README.md](../../README.md)。

输出样例截图见 [examples/sample-pics/](../sample-pics/)。

---

# Show folder template (`your-show`)

This folder has **no real video**. Put your media **beside** `video-pics-cut` (sibling), not inside the repo.

## Layout

```text
<workspace>/
├── video-pics-cut/
└── your-show/                 ← any show name
    ├── 01/
    │   ├── Show.Name.S01E01.mkv
    │   └── 01-pics/           ← created by the tool
    ├── 02/ … 02-pics/
    └── reference/             ← optional stills for style re-measure
```

## Rules

| Item | Convention |
| --- | --- |
| Episode folder | Two digits: `01`, `02`, … → output `01-pics` |
| Video name | Include `SxxExx` (e.g. `S01E01`) |
| Subtitles | Soft text subtitles required (not burned-in / not PGS) |
| Output | `{episode}-pics\`: `*.jpg` + `_index.csv` + `_report.txt` |

## Run

1. Place one episode video as above  
2. In `video-pics-cut`, double-click `scripts\run_gui.bat`, select `your-show\01`  
3. Or: `python main.py --input "..\your-show\01" --max-cues 3`

See [QUICKSTART.md](../../QUICKSTART.md) and [README.md](../../README.md).
