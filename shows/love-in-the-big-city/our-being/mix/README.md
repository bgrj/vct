# 影片启蒙｜不得人爱，便无法爱人

《大都市的爱情法》1–8 集剧照混剪。主题句是第 2 集金南圭墓志原词，不改字。

| 项 | 值 |
| --- | --- | --- | --- |
| 成片 | `不得人爱便无法爱人.mp4` |
| 规格 | 1920×1080 · 24 fps · 约 3 分 01 秒 |
| 素材 | `shows/love-in-the-big-city/01`–`08` 已烧字幕剧照 |
| 镜数 | 65 张剧照 + 5 张标题卡 |
| 时间线 | `edl.csv` · Concat 工程 `concat.json` |

## 结构

1. 片头 → 当兵的男友（轻）
2. 墓：故金南圭 → **不得人爱，便无法爱人**
3. 金南圭：相遇、约会、KTV、艾略特、若偏执不是爱情
4. 卢英洙：斯宾诺莎、《伦理学》、孤独、关系很难说明
5. 沈圭浩／冷猫：护理班、上海、已婚、九年前
6. 回到墓志原词

剧照上已有字幕，成片不再叠字。未使用剧集原声；垫乐是本目录生成的极弱原声 pad，不是片中配乐。

## Concat

本环境不能启动 Concat 窗口（`concat --mcp` 是本机进程）。工程按 Concat 的 `concat.json` 写出，时间线和 Concat 的 `clips_add_sequence` 同一条轴。

本机已装 Concat 时：

1. 把本目录做成 Concat 工程文件夹
2. 若媒体显示缺失，按 `edl.csv` 把 `../../{集号}/{filename}` 链回剧照
3. 或改 `concat.jsonl` 里的 `MEDIA_ROOT` 后：`concat-cli api < concat.jsonl`

## 重新导出

仓库根目录的 `.gitignore` 默认忽略 `*.mp4`。本目录的成片靠下面这条例外才能提交：

```
!shows/love-in-the-big-city/our-being/mix/*.mp4
```
