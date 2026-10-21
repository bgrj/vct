# 八个参考项目：读了什么，实际用了什么

这不是“运行了八套视频工具”的声明，也不是对模型进行训练。以下为本次实际研读的具体文件及改动落点；只提取适合真实剧照主题剪辑的方法，不套用产品宣传片的动效。没有生成演员动作，没有伪造剧集原声，没有调用付费视频接口。

| 项目与实际阅读 | 提取的方法 | 第三版的具体落点 | 明确不用的部分 |
|---|---|---|---|
| [PromptLens](https://github.com/bgrj/prompt-lens)：README、lib/ai/prompts/zh.ts、lib/audio-processor/llm-segmenter.ts | 主体、环境、景别、光线、情绪及序列连贯性分开检查；分段围绕语义 | 每个镜头写 why；第33格原先“嬉笑跑动+失去独白”不协调，改用同集独处镜头承接原词 | 不运行云端反推，不生成假表演 |
| [opus-video-prompts](https://github.com/bgrj/opus-video-prompts)：README 的高端极简产品片与写法经验 | 一镜一个意图；先时间线后代码；真实素材；实测落点；关键帧返工 | 无品牌长片头、无震屏闪白；先生成47格实际渲染样帧再整片导出 | 不套产品 UI、光效和广告式节奏 |
| [ai-shortfilm-prompts](https://github.com/bgrj/ai-shortfilm-prompts)：methodology.zh.md、templates/music-video.zh.md、templates/multi-shot-narrative.zh.md | verse 的停留、hook 的密度、表情与反应作为切点；不无动机乱剪 | 快段只用四格无字回忆；长句后保留反应；有开场钩子和收束 | 不把12秒MV模板硬拉成225秒；不生成演员镜头 |
| [Seedance 2.5 Video Director](https://github.com/bgrj/seedance-2-5-video-director)：README、references/realistic-direction-patterns.md | 刺激—停顿—反应—关系变化；冲突要同时给双方愿望；留听者反应 | “每天待在一起”与“不是那种待在一起”相邻；保留“我也想跟你好好生活”；否定后不立刻跳场 | 不调用Seedance；不把静态图片宣称成真实人物表演 |
| [OpenMontage](https://github.com/bgrj/OpenMontage)：README、docs/bgm-montage-lite.md、music-to-video/references/montage.md、documentary-montage/edit-director.md | 节奏、并置、配乐、视觉语域一起检查；hero长停留；切与叠化的小词汇表 | 长停留约7.3秒；轻推拉、慢横移；音乐自然衰减才进入墓地；不改用户选曲 | 不启用付费素材或视频生成pipeline；不使用glitch转场包 |
| [video-shotcraft](https://github.com/bgrj/video-shotcraft)：SKILL、music-beat-sync、aesthetic-rules、sound-design、final-review | 拟合真实拍间隔与相位；区分瞬态真值和帧量化；输出音轨偏移回测；卡点不等于震屏 | 全曲实测约131.997BPM；统一30fps边界；切点/叠化中心锚到检测瞬态；编码后做4段交叉相关回测 | 不逐拍冲击；不套产品发布会、花字、品牌合影结尾 |
| [OpenShot](https://github.com/bgrj/openshot-qt)：README、doc/animation.rst、doc/transitions.rst | 缓入缓出关键帧；过渡透明度而非硬跳变 | 推拉使用smoothstep曲线；短叠化使用余弦混合，保持固定画幅和字幕 | 没有在本环境运行OpenShot图形程序，不宣称有.osp验证 |
| [Lemo-Opuscar](https://github.com/lemomo-ai/lemo-opuscar)：README.zh-CN、DIRECTOR、TECHNIQUE | 先treatment；单一情绪转弯；音画共用一条时间线；阅读时长和回测 | 三候选结构先取舍；0秒人物、结尾回题；timeline.json驱动全部；字幕按阅读速度核验 | 不照抄demo角色、旋律与签名；无原声不能假做J/L对白剪辑 |

## 不可掩盖的限制

- 原仓库1—8集是剧照与CSV，不包含动态视频。此版本只能是**有镜头运动的剧照蒙太奇**，不会等价于演员动作与对白的影视混剪。
- 舞曲可以与渴望、靠近和回望形成对照，但不能仅靠卡点把其音色变成挽歌。保留本曲是遵循用户既有选择；若以后允许换曲，需另做情绪版而不是偷偷替换。
- Concat官方Linux 0.2.8发行版与实际二进制均测试失败，均要求GLIBC_2.35；本机系统不满足。成片由FFmpeg渲染；提供依据公开模型写出的Concat工程，**未做Concat运行时/界面验证**。
- 客观对齐测量与关键帧检查，不等于“完整有声试看”或对情绪质量的保证。
- 128kb/s MP3无法通过256kb/s输出恢复被丢弃的细节；本次处理仅管理响度、峰值及很低的次声。
