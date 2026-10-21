# 不得人爱，便无法爱人 · V3

第三版：**3:45.6 · 1920×1080 · 30fps · 有声剧照蒙太奇**。保留T-ara《SEXY LOVE》的原速与完整时长。

## 这版重点改什么

不是在第二版上加转场，而是重写“渴望—拒绝—曾被接纳—失去—重新辨认”的推进。47格镜头来自第1—8集；33组原词字幕；绝大多数镜头约3.6—7.3秒，只有四格无字回忆加速。葬礼与题句移到原曲自然释放段。

- [视频文件](output/不得人爱便无法爱人_v3.mp4)（线上构建完成后出现）
- [导演方案](TREATMENT.md)
- [八个参考项目及落实情况](REFERENCE_NOTES.md)
- [镜头表](edl.csv) / [单一时间线](timeline.json)
- [技术验收](analysis/qa_report.json)（导出并验收后生成）

## 复现

需要Python 3.12+、FFmpeg、中文Noto字体。下载本目录，运行：

```bash
python -m pip install -r requirements.txt
python build.py --workers 2
python verify.py
python make_concat.py
```

素材按timeline.json里的精确URL下载到assets/，只下载选定剧照，不改01—08原目录。样帧可单独生成：`python build.py --preview 0 11 23 29 33 41 46`。

GitHub Actions若获仓库许可，会按同一脚本导出、验收并提交MP4，不调用付费服务。仅修改本V3目录和本次专用工作流；不会改前两版或Notion草稿。

## 可编辑工程

`python make_concat.py`生成`concat.json`。画面的轻推拉和短叠化已预渲染为assets/segments/；字幕为Concat原生文本轨，配乐为独立音轨。需要先完成build，或使用包含媒体的工程压缩包。

**Concat官方0.2.8在此Linux环境因GLIBC_2.35依赖失败；本工程依据公开格式生成，未在Concat界面中验证。FFmpeg成片与timeline.json为本次交付基准。**

## 素材和音质边界

这是剧照，不是动态剧集片源；没有原声对白，没有生成演员动作。原曲为128kb/s MP3，AAC 256kb/s仅避免过低输出码率，并非无损修复。响度与峰值结果以qa_report.json为准。素材权利说明见[CREDITS](CREDITS.md)，公开传播前请核实授权。
