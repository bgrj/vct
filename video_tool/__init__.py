# -*- coding: utf-8 -*-
"""视频台词智能截图工具包

对外暴露主要入口，main.py 只需导入本包即可使用全部能力：
* 均匀时间去帧截图（原有能力，见 main.py 的 extract_frames）
* 按内嵌字幕时间轴截取台词画面（新增能力，见 pipeline.process_video / run）
"""

from . import config, ffmpeg_utils, pipeline, quality, renderer, subtitles
from .config import APP_TITLE, APP_VERSION, VideoToolError
from .pipeline import DialogueOptions, Reporter, RunResult, process_video, run

__all__ = [
    'APP_TITLE',
    'APP_VERSION',
    'VideoToolError',
    'config',
    'ffmpeg_utils',
    'pipeline',
    'quality',
    'renderer',
    'subtitles',
    'DialogueOptions',
    'Reporter',
    'RunResult',
    'process_video',
    'run',
]
