# 本地视频自动化截图工具（Video Pics Cut）

**一句话定位**：把一集带中文字幕的视频交给它，它会自动帮你「**每有一句台词，就截一张图**」，
把这句话以统一的样式印在画面底部，并按顺序命名保存好。

- 非技术用户：先看 **[QUICKSTART.md](QUICKSTART.md)**，双击 `scripts\run_gui.bat` 即可
- 开发者 / CLI：继续阅读下文

---

## 1. 这个工具帮你做什么

| 手动做 | 实际结果 |
| --- | --- |
| 一集几百句台词，一句句拖进度条截图 | 做不完 |
| 随手暂停就截 | 经常截到眨眼、张嘴、糊帧 |
| 画面和台词对不上 | 回头看不出这张图对应哪句话 |
| 不同字幕格式截的图 | 字号、位置不统一 |

本工具自动完成：读软字幕 → 每句多样本选优 → 印台词 → 有序命名 → 生成索引与报告。

支持两种模式：

- **台词智能截图**（默认）：按字幕时间轴逐句选优；可补「无台词空窗」剧情延续帧
- **均匀截图**：在视频时长内均匀取帧

**重点（眼睛比嘴巴更重要）**：优先挑睁眼、双眼开合对称的帧，再兼顾口型与清晰度。

## 2. 主要功能特点

### 台词智能截图

- 自动识别中文字幕轨（优先简体中文），按时间轴定位，无 OCR
- 每句采样多张候选（默认 5~10），综合清晰度、运动、人像自然度（眼睛 / 嘴部）、构图与时序选优
- 闭眼重采样：整句都闭眼时临时提高采样密度再挑一次
- 无人像空镜照常出图，索引中标记降级
- **剧情延续帧**：相邻台词间隔 ≥ 6 秒的空窗自动补截，句序为「前接句序号 4 位 + a/b/c」（如 `0004a`），与台词帧同目录、同索引
- 台词样式来自仓库内 `style_profile.json`（可用参考图重算）
- `_index.csv` + `_report.txt`；断点续跑；`--clean` 重建；跳过未下完文件

### 共用能力

- GUI + CLI；递归搜视频；进度与取消；单句失败不中断整集

## 3. 它做不到什么

| 做不到 | 怎么办 |
| --- | --- |
| 无软字幕 / 仅硬字幕 / 仅图形字幕（PGS） | 换带文字型软字幕的片源 |
| 100% 完美神态 | 看报告抽查；剧情帧需人工语义复核 |
| PNG 默认输出 | 改 `video_tool/config.py` 的 `IMAGE_FORMAT` 后对该集 `--clean` |

处理视频全程本机完成；联网仅用于首次装依赖或补下人脸模型。

## 4. 运行前提（装一次）

- **Python 3.12**（mediapipe 对 3.13 支持滞后）
- **FFmpeg / FFprobe**：加入 PATH，或放在 `C:\ffmpeg\bin`、`D:\ffmpeg\bin` 等常见位置（程序会自动查找）
- 人脸模型：`models/face_landmarker.task`（随仓库提供）
- 中文字体：优先微软雅黑等系统字体

```bash
cd "Video Pics Cut"
python -m pip install -r requirements.txt
python -c "import cv2, numpy, PIL, mediapipe; print('deps ok')"
python main.py --help
```

Windows 也可双击 `scripts\check_env.bat`。

## 5. 素材目录怎么摆

**一集视频一个文件夹**；输出目录 = `文件夹名 + "-pc"`（自动创建）。

```text
<工作区>/
├── Video Pics Cut/                 ← 工具本体
└── YourShow/                       ← 任意剧名，与工具并列
    ├── 01/
    │   ├── Show.Name.S01E01.mkv    ← 文件名须含 SxxExx
    │   └── 01-pc/                  ← 跑完自动出现
    ├── 02/ … 02-pc/
    └── reference/                  ← 可选：人工参考图（仅 analyze_reference）
```

- 推荐单集文件夹用两位数字：`01`、`02`…
- 视频名含 `S01E03` 时，图片前缀为 `S01E03`；否则回退为视频主名
- 未下完文件（`.part` 等）会跳过
- 布局示例说明：[examples/YourShow/README.md](examples/YourShow/README.md)

## 6. 输出规则与文件命名

- **输出目录**：如 `...\YourShow\03\xx.mkv` → `...\YourShow\03\03-pc\`
- **工具原生命名（请以此为准）**：
  - 台词帧：`S01E03_0007_00-12-34.567.jpg`（集号_四位句序_时间码）
  - 剧情帧：`S01E03_0004a_00-31-12.500.jpg`（前接句序 + a/b/c；片头用 `0000a`）
- **可选：场景描述后缀**（人工/批量后处理，**不是**工具自动写出）：
  - 推荐形态（可断点续跑）：`S01E03_0007_00-12-34.567_角色推门进屋.jpg`
  - 严格形态（去掉句序）：会破坏续跑，须整集确认后改，重跑须 `--clean`
  - 描述：中文短语，勿含空格与 `\ / : * ? " < > |`
- **`_index.csv`**（utf-8-sig）：句序、台词、文件名、眼睛状态等；剧情帧句序为 `0004a` 形式
- **`_report.txt`**：汇总与验收结论
- **`_audit.csv`**：剧情帧质量审计副产物（见第 10 节）
- 交付目录理想上仅有：`*.jpg`、`_index.csv`、`_report.txt`

「降级」= 无人像或平坦画面，图仍保留，属预期。

## 7. 使用方式一：图形界面

```bash
python main.py
```

或双击 `scripts\run_gui.bat`。

1. 选择**单集视频所在文件夹**（如 `YourShow\03`）
2. 输出自动为 `03-pc`
3. 可调字幕轨、候选帧数、试跑句数、`--clean` 等
4. 开始处理后看「第 N/M 句」进度

均匀模式：输出建议为父目录下 `<文件夹名>_output`，可改。

## 8. 使用方式二：命令行与脚本

### 8.1 常用命令

```bash
# 处理一集（路径含空格必须加双引号）
python main.py --input "..\YourShow\03"

# 先试跑前 3 句
python main.py --input "..\YourShow\03" --max-cues 3

# 从第 41 句起再跑 3 句
python main.py --input "..\YourShow\03" --start-cue 41 --max-cues 3

# 重建整集
python main.py --input "..\YourShow\03" --clean

# 不补剧情帧 / 调整空窗
python main.py --input "..\YourShow\03" --no-story-gaps
python main.py --input "..\YourShow\03" --story-gap-min 8 --story-gap-max-per 2

# 均匀截图
python main.py --input "..\某文件夹" --mode uniform --frames 20
```

也可拖拽单集文件夹到 `scripts\run_episode.bat`，或：

```bat
scripts\run_episode.bat "..\YourShow\03"
```

长时间任务建议重定向日志到文件（脚本已写入 `_run.log`）。

### 8.2 完整参数表

| 参数 | 说明 | 默认 |
| --- | --- | --- |
| `--input` | 单集目录（缺省则开 GUI） | 无 |
| `--mode` | `dialogue` / `uniform` | `dialogue` |
| `--output` | 均匀模式输出目录 | `<目录名>_output` |
| `--frames` | 均匀截图数量 | `8` |
| `--subtitle-track` | 字幕轨，`auto` 优先简体中文 | `auto` |
| `--candidate-max` | 每句候选上限 | `10` |
| `--start-cue` / `--max-cues` | 起始句 / 只处理前 N 句（0=全部） | `1` / `0` |
| `--no-subtitle-text` | 不烧字幕 | 关 |
| `--no-resume` | 不跳过已有图 | 关 |
| `--clean` | 清空本集图与索引再生成 | 关 |
| `--eye-refine` | `closed` / `half` / `off` | `closed` |
| `--no-story-gaps` | 不补剧情帧 | 关 |
| `--story-gap-min` / `--story-gap-max-per` | 空窗阈值秒 / 每窗上限 | `6.0` / `3` |

### 8.3 PowerShell

行首若是带引号的 exe，前面加 `& `。`cd /d` 换成 `Set-Location`。

## 9. 使用方式三：让 AI 助手指挥

把本工具目录打开为工作区，让 AI 执行、你验收。建议：先 `--max-cues 3` 看效果 → 确认后整集 → 复核剧情帧 → **一集一集确认**，勿自动连跑。

剧情理解提示（可复制给 AI）：

```text
除台词帧外，请重视无台词但承接剧情的画面（反应镜头、信物特写、回忆空镜等）。
工具会按空窗自动补截剧情帧（句序如 0004a）；请逐张复核去留，不必手工找画面。
```

## 10. 跑完怎么验收

### 10.1 三条对照

1. **`_report.txt`**：成功出图 = 台词总句数；失败 = 0；缺失 = 0；正式整集结论为「通过」或「基本通过（须目检）」
2. **`_index.csv`**：台词行数与台词图一致；剧情帧「文件名」为空表示已人工淘汰（台账行，正常）
3. **抽 2 张图**：一张有人脸睁眼、一张无人像空镜

### 10.2 交付清单（摘要）

- 三量对齐；命名符合第 6 节；样式一致；剧情帧已复核；目录干净（无临时日志）

### 10.3 汇报模板

```text
集号：第 NN 集　｜　产物目录：...\YourShow\NN\NN-pc
视频文件：Show.Name.S01ENN....mkv　｜　字幕轨：…
台词总句数：XXX　｜　台词图片数：XXX　｜　索引台词行数：XXX
成功出图：XXX　｜　降级：XXX　｜　失败：0
眼睛状态：睁开 XX　｜　半闭 XX　｜　闭合 XX
剧情延续帧：空窗 XX，补截 XX，复核保留 XX；清单：时间码 + 内容 + 保留理由
遗留问题：无 / …
请确认本集是否通过后再开始下一集。
```

### 10.4 剧情延续帧复核

1. 读 `_report.txt` 空窗明细
2. 审计：`python tools/audit_story_quality.py --input "..\YourShow\08"` → `08-pc\_audit.csv`
3. 逐张看语义是否承载剧情
4. 批量删建议项：`python tools/apply_audit_deletions.py --input "..\YourShow\08" --recommend 建议删除`（可用 `--dry-run`）
5. 重补某空窗：删掉索引中对应剧情行后再跑（台词图会跳过）

硬 flag 示例：模糊 / 极暗 / 黑场卡 / 同空窗近重复 → 多建议删除；语义最终以人工为准。

### 10.5 旧命名迁移（仅历史产物）

若某集剧情帧仍是旧式 `S01E08_X01_….jpg`：

```bash
python tools/migrate_story_naming.py --input "..\YourShow\08" --dry-run
python tools/migrate_story_naming.py --input "..\YourShow\08"
```

只改名与索引，不重抽帧。新跑的集不需要。

## 11. 常见问题

| 现象 | 怎么办 |
| --- | --- |
| `unrecognized arguments: …` | 路径含空格未加引号 |
| 找不到字幕 | 换软字幕片源 |
| 跳过未下载完成 | 下完再跑 |
| 图片数少于台词 | 再跑一次自动补缺 |
| 新旧样式混在一起 | `--clean` |
| 中文方块 | 安装中文字体 |
| 满屏 WARNING | 可忽略，看报告汇总 |
| 删了剧情图但 CSV 还有行 | 台账行，防重补；要重补就删该行 |
| 半闭眼偏多 | `--candidate-max 15` 或 `--eye-refine half` |

**红线**：不上传素材到在线 API；不擅自改原始视频；逐集确认后再开下一集；样式变更必须 `--clean`；路径含空格必须加引号。

## 12. 术语对照

| 词 | 意思 |
| --- | --- |
| 软字幕 | 可提取成文字的内嵌字幕 |
| 候选帧 | 一句内多张画面，从中择优 |
| 降级 | 无人像等，图保留仅标注 |
| 断点续跑 | 已有图跳过 |
| 剧情延续帧 | 无台词空窗补截，句序如 `0004a` |
| 样式基线 | `style_profile.json` 中的字号/描边/贴底比例 |

## 13. 台词样式基线

仓库已带默认 `style_profile.json`。换剧或要改字幕视觉时，准备参考截图目录后重算：

```bash
python tools/analyze_reference.py --reference "..\YourShow\reference"
python tools/analyze_reference.py --reference "..\YourShow\reference" --group 某子目录 --limit 60 --print-only
```

`--reference` **必填**。基线缺失时回退 `config.py` 内置默认值。

## 14. 项目结构与许可证

```text
Video Pics Cut/
├── main.py
├── QUICKSTART.md
├── README.md
├── video_tool/
├── tools/
├── scripts/                  # Windows 一键：run_gui / run_episode / check_env
├── examples/YourShow/        # 通用素材布局说明（无片源）
├── docs/                     # 可选归档（非模板必读）
├── style_profile.json
├── models/face_landmarker.task
└── requirements.txt
```

© 2026-2028 Ourbeing (https://ourbeings.com)　｜　MIT License

附录（非必读）：[docs/HISTORY_Love_in_the_big_City.md](docs/HISTORY_Love_in_the_big_City.md) 仅归档某历史项目进度。
