import argparse
import subprocess
import os
import sys
import tkinter as tk
from tkinter import filedialog, ttk, messagebox
import threading

# 新增能力所在的功能包（字幕解析、候选帧评分、台词渲染、流程编排）
from video_tool import config as tool_config
from video_tool import ffmpeg_utils, pipeline


def is_video_file(file_path):
    """检查文件是否是视频文件"""
    # 实现已下沉到 video_tool.ffmpeg_utils（可自动定位 ffmpeg，无需依赖系统 PATH）
    return ffmpeg_utils.is_video_file(file_path)

def extract_frames(video_path, output_dir, num_frames, progress_callback=None):
    # 创建输出目录
    os.makedirs(output_dir, exist_ok=True)

    # 使用 FFmpeg 获取视频时长
    ffprobe_cmd = [ffmpeg_utils.ffprobe_path(), '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', video_path]
    duration = float(subprocess.check_output(ffprobe_cmd).decode().strip())

    # 获取视频帧率
    ffprobe_fps_cmd = [ffmpeg_utils.ffprobe_path(), '-v', 'error', '-select_streams', 'v', '-show_entries', 'stream=r_frame_rate', '-of', 'default=noprint_wrappers=1:nokey=1', video_path]
    fps_str = subprocess.check_output(ffprobe_fps_cmd).decode().strip()
    
    # 解析帧率（格式可能是 "24/1"）
    if '/' in fps_str:
        num, den = map(int, fps_str.split('/'))
        fps = num / den if den != 0 else 0
    else:
        fps = float(fps_str) if fps_str else 0
    
    # 估算总帧数
    total_frames = int(duration * fps) if fps > 0 else 0
    
    # 调整截图数量，确保不超过视频总帧数的80%
    if total_frames > 0 and num_frames > total_frames * 0.8:
        adjusted_num_frames = max(1, int(total_frames * 0.8))
        if progress_callback:
            progress_callback(0)  # 重置进度条
        return adjusted_num_frames, False  # 返回调整后的帧数和标志表示需要调整
    
    # 计算时间间隔（确保不会太小）
    min_interval = 0.1  # 最小间隔为0.1秒
    if duration <= num_frames * min_interval:
        # 视频太短，调整间隔
        interval = max(min_interval, duration / (num_frames + 1))
    else:
        interval = duration / (num_frames + 1)
    
    # 提取帧
    try:
        for i in range(1, num_frames + 1):
            time_pos = i * interval
            minutes = int(time_pos // 60)
            seconds = int(time_pos % 60)
            milliseconds = int((time_pos % 1) * 1000)
            output_file = os.path.join(output_dir, f'{os.path.splitext(os.path.basename(video_path))[0]}_time_{minutes:02d}m{seconds:02d}s{milliseconds:03d}.jpg')
            
            # 添加超时控制
            ffmpeg_cmd = [ffmpeg_utils.ffmpeg_path(), '-ss', str(time_pos), '-i', video_path, '-vframes', '1', '-q:v', '2', output_file]
            try:
                # 设置超时时间为10秒
                subprocess.run(ffmpeg_cmd, timeout=10)
            except subprocess.TimeoutExpired:
                print(f"提取帧 {i} 超时，跳过")
                continue
            
            # 更新进度
            if progress_callback:
                progress_callback(i / num_frames * 100)
    except Exception as e:
        print(f"提取帧时出错: {str(e)}")
        raise
    
    return num_frames, True  # 返回实际使用的帧数和成功标志


HELP_TEXT_UNIFORM = """使用步骤（均匀截图）：
1. 点击"浏览..."按钮选择包含视频文件的文件夹
2. 程序会自动设置输出文件夹，您也可以点击"浏览..."按钮自定义输出位置
3. 设置每个视频需要截取的图片数量（默认为8张）
4. 点击"开始处理"按钮开始批量处理视频

注意事项：
· 程序会递归搜索所选文件夹中的所有视频文件
· 截图会均匀分布在视频时长内
· 对于较短的视频，程序会自动调整截图数量
· 处理过程中可以通过进度条查看当前进度
· 需要安装FFmpeg才能正常使用本工具"""

HELP_TEXT_DIALOGUE = """使用步骤（台词智能截图）：
1. 点击"浏览..."按钮选择【单集视频所在的文件夹】（如 ...\\Love in the big City\\03）
2. 输出目录自动确定为该文件夹内的 03-pc，无需手工设置
3. 按需调整字幕轨道、每句候选帧数等参数
4. 点击"开始处理"按钮开始处理，界面显示"第 N/M 句"进度

工作原理：
· 读取视频的【内嵌软字幕】（优先简体中文），取得每句台词的起止时间
· 在每句台词的时间区间内采样多张候选画面，综合画面清晰度、运动幅度、
  人像自然度（睁眼程度、嘴部说话状态）与情境匹配（构图居中、贴近句子中点、
  与句子中点同镜头）打分，只保留得分最高的一张
· 在画面底部以粗体白字 + 黑描边（无底色）渲染该句台词，字号、描边宽度与
  贴底位置取自参考素材统计出的样式基线 style_profile.json
· 无人像的空镜、背影、字幕卡（即使画面全黑）同样正常出图，只是标记为降级
· 整句候选都没睁眼时自动重采样一次（提高候选帧数），把闭眼帧换成睁眼帧
· 台词之间的无台词空窗（间隔 ≥ 6 秒，含片头/片尾）自动补截「剧情延续帧」：
  承载前文台词情感或情节延伸的画面（反应镜头、物件特写、回忆闪回等）。
  同一空窗按镜头去重后挑画质最优的代表画面，每窗至多 3 张，句序记为
  「前接句序号 4 位 + 子后缀 a/b/c」（如 0004a、0298b），按文件名字母序
  天然插在对应台词图之间；写入 _index.csv；是否承载剧情需人工复核，
  删除后重跑不会重复补截（索引保留台账）
· 收尾做完整性校验（字幕句数 / 图片数 / 索引行数），缺图的句子自动补跑
· 输出目录内生成 _index.csv（台词索引表）与 _report.txt（处理报告）
· 图片命名如 S01E03_0007_00-12-34.567.jpg，按句序与时间码有序排列

要处理下一集，把输入目录改成 04 即可（输出为 04\\04-pc）。"""


class GuiReporter(pipeline.Reporter):
    """把流程编排的进度与状态回调转发到 tkinter 界面"""

    def __init__(self, app):
        self.app = app

    def status(self, text):
        self.app.update_status(text)

    def progress(self, value):
        self.app.update_progress(value)

    def cancelled(self):
        return self.app.cancel_requested


class VideoFrameExtractorApp:
    def __init__(self, root):
        self.root = root
        self.root.title(tool_config.APP_TITLE)
        self.root.geometry("780x840")
        self.root.resizable(True, True)
        
        self.input_dir = ""
        self.output_dir = ""
        self.num_frames = tk.IntVar(value=8)
        self.processing = False
        self.cancel_requested = False
        
        # 截图模式：dialogue = 台词智能截图（新增），uniform = 原有均匀截图
        self.mode = tk.StringVar(value="dialogue")
        # 台词截图模式参数
        self.subtitle_track = tk.StringVar(value="auto")
        self.candidate_max = tk.IntVar(value=tool_config.CANDIDATE_MAX)
        self.max_cues = tk.IntVar(value=0)
        self.burn_subtitle = tk.BooleanVar(value=True)
        # 重建：先清空该集输出目录里的历史图片与旧索引，避免新旧样式混在一起
        self.clean_output = tk.BooleanVar(value=False)
        # 闭眼帧重采样口径：整句都没睁眼时把候选帧数临时调高，再挑一次
        self.eye_refine = tk.StringVar(value="closed")
        # 剧情延续帧：台词之间的无台词空窗（反应镜头、物件特写等）系统化补截
        self.story_gaps = tk.BooleanVar(value=True)
        self.story_gap_min = tk.DoubleVar(value=tool_config.STORY_GAP_MIN)
        self.story_gap_max_per = tk.IntVar(value=tool_config.STORY_GAP_MAX_PER_GAP)
        
        self.create_widgets()
    
    def create_widgets(self):
        # 创建主框架
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 添加使用说明区域
        help_frame = ttk.LabelFrame(main_frame, text="使用说明", padding="5")
        help_frame.pack(fill=tk.X, pady=5)
        
        self.help_text_var = tk.StringVar(value=HELP_TEXT_DIALOGUE)
        help_label = ttk.Label(help_frame, textvariable=self.help_text_var, justify=tk.LEFT, wraplength=720)
        help_label.pack(fill=tk.X, padx=5, pady=5)
        
        # 截图模式选择
        mode_frame = ttk.LabelFrame(main_frame, text="截图模式", padding="5")
        mode_frame.pack(fill=tk.X, pady=5)
        
        ttk.Radiobutton(mode_frame, text="台词智能截图（按内嵌字幕时间轴，每句挑选最优画面）",
                        variable=self.mode, value="dialogue",
                        command=self.on_mode_changed).grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        ttk.Radiobutton(mode_frame, text="均匀截图（原有功能，在视频时长内均匀取帧）",
                        variable=self.mode, value="uniform",
                        command=self.on_mode_changed).grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        
        # 输入文件夹选择
        input_frame = ttk.LabelFrame(main_frame, text="输入设置", padding="5")
        input_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(input_frame, text="导入文件夹:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.input_dir_entry = ttk.Entry(input_frame, width=50)
        self.input_dir_entry.grid(row=0, column=1, sticky=tk.W+tk.E, padx=5, pady=5)
        ttk.Button(input_frame, text="浏览...", command=self.select_input_dir).grid(row=0, column=2, padx=5, pady=5)
        
        # 输出文件夹选择
        self.output_frame = ttk.LabelFrame(main_frame, text="输出设置", padding="5")
        self.output_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(self.output_frame, text="导出文件夹:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.output_dir_entry = ttk.Entry(self.output_frame, width=50)
        self.output_dir_entry.grid(row=0, column=1, sticky=tk.W+tk.E, padx=5, pady=5)
        self.output_browse_button = ttk.Button(self.output_frame, text="浏览...", command=self.select_output_dir)
        self.output_browse_button.grid(row=0, column=2, padx=5, pady=5)
        
        # 截图数量设置（均匀截图模式）
        self.uniform_frame = ttk.LabelFrame(main_frame, text="均匀截图设置", padding="5")
        self.uniform_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(self.uniform_frame, text="截取数量:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Spinbox(self.uniform_frame, from_=1, to=1000, textvariable=self.num_frames, width=10).grid(row=0, column=1, sticky=tk.W, padx=5, pady=5)
        
        # 台词截图设置（台词智能截图模式）
        self.dialogue_frame = ttk.LabelFrame(main_frame, text="台词截图设置", padding="5")
        self.dialogue_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(self.dialogue_frame, text="字幕轨道:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Combobox(self.dialogue_frame, textvariable=self.subtitle_track, width=14, state="readonly",
                     values=("auto", "简体中文", "繁体中文")).grid(row=0, column=1, sticky=tk.W, padx=5, pady=5)
        ttk.Label(self.dialogue_frame, text="auto 为自动选择，优先简体中文").grid(row=0, column=2, sticky=tk.W, padx=5, pady=5)
        
        ttk.Label(self.dialogue_frame, text="每句候选帧:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Spinbox(self.dialogue_frame, from_=tool_config.CANDIDATE_MIN, to=30, textvariable=self.candidate_max,
                    width=10).grid(row=1, column=1, sticky=tk.W, padx=5, pady=5)
        ttk.Label(self.dialogue_frame, text="候选越多越容易挑到睁眼且清晰的帧，耗时略增").grid(row=1, column=2, sticky=tk.W, padx=5, pady=5)
        
        ttk.Label(self.dialogue_frame, text="只处理前几句:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Spinbox(self.dialogue_frame, from_=0, to=100000, textvariable=self.max_cues, width=10).grid(row=2, column=1, sticky=tk.W, padx=5, pady=5)
        ttk.Label(self.dialogue_frame, text="0 表示全部；先填 3 可快速试看效果").grid(row=2, column=2, sticky=tk.W, padx=5, pady=5)
        
        ttk.Checkbutton(self.dialogue_frame, text="在画面底部渲染台词文字（白色描边字，无底色，样式取自参考素材基线）",
                        variable=self.burn_subtitle).grid(row=3, column=0, columnspan=3, sticky=tk.W, padx=5, pady=5)
        
        ttk.Checkbutton(self.dialogue_frame, text="清空输出目录后重建（删除该集历史图片与旧索引，避免新旧样式混在一起）",
                        variable=self.clean_output).grid(row=4, column=0, columnspan=3, sticky=tk.W, padx=5, pady=5)
        
        ttk.Label(self.dialogue_frame, text="闭眼帧重采样:").grid(row=5, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Combobox(self.dialogue_frame, textvariable=self.eye_refine, width=12, state="readonly",
                     values=tool_config.EYE_REFINE_MODES).grid(row=5, column=1, sticky=tk.W, padx=5, pady=5)
        ttk.Label(self.dialogue_frame, text="closed 只救「整句都闭眼」；half 连半闭一起救；off 关闭").grid(
            row=5, column=2, sticky=tk.W, padx=5, pady=5)
        
        ttk.Checkbutton(self.dialogue_frame, text="补截剧情延续帧（台词之间的无台词空窗：反应镜头、物件特写等，句序 0004a/b/c…，需人工复核去留）",
                        variable=self.story_gaps).grid(row=6, column=0, columnspan=3, sticky=tk.W, padx=5, pady=5)
        
        ttk.Label(self.dialogue_frame, text="空窗阈值(秒):").grid(row=7, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Spinbox(self.dialogue_frame, from_=1.0, to=120.0, increment=0.5,
                    textvariable=self.story_gap_min, width=10).grid(row=7, column=1, sticky=tk.W, padx=5, pady=5)
        ttk.Label(self.dialogue_frame, text="相邻台词间隔达到该秒数才补截（含片头/片尾）").grid(
            row=7, column=2, sticky=tk.W, padx=5, pady=5)
        
        ttk.Label(self.dialogue_frame, text="每空窗上限:").grid(row=8, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Spinbox(self.dialogue_frame, from_=1, to=10, textvariable=self.story_gap_max_per,
                    width=10).grid(row=8, column=1, sticky=tk.W, padx=5, pady=5)
        ttk.Label(self.dialogue_frame, text="镜头去重后每个空窗最多出几张").grid(
            row=8, column=2, sticky=tk.W, padx=5, pady=5)
        
        # 进度条
        self.progress_frame = ttk.LabelFrame(main_frame, text="处理进度", padding="5")
        self.progress_frame.pack(fill=tk.X, pady=5)
        
        self.progress = ttk.Progressbar(self.progress_frame, orient=tk.HORIZONTAL, length=100, mode='determinate')
        self.progress.pack(fill=tk.X, padx=5, pady=5)
        
        self.status_label = ttk.Label(self.progress_frame, text="就绪")
        self.status_label.pack(anchor=tk.W, padx=5, pady=5)
        
        # 按钮区域
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=10)
        
        self.start_button = ttk.Button(button_frame, text="开始处理", command=self.start_processing)
        self.start_button.pack(side=tk.RIGHT, padx=5)
        
        self.cancel_button = ttk.Button(button_frame, text="取消", command=self.cancel_processing, state=tk.DISABLED)
        self.cancel_button.pack(side=tk.RIGHT, padx=5)
        
        # 添加版权信息
        copyright_label = ttk.Label(main_frame, text=tool_config.COPYRIGHT, font=("Arial", 9))
        copyright_label.pack(side=tk.BOTTOM, pady=5)
        
        # 按当前模式初始化界面状态
        self.on_mode_changed()
    
    def on_mode_changed(self):
        """切换截图模式时更新说明文字与相关设置项的可用状态"""
        dialogue_mode = self.mode.get() == "dialogue"
        
        self.help_text_var.set(HELP_TEXT_DIALOGUE if dialogue_mode else HELP_TEXT_UNIFORM)
        
        # 台词模式的输出目录由「视频所在目录名 + -pc」自动推导，屏蔽手工指定
        entry_state = tk.DISABLED if dialogue_mode else tk.NORMAL
        self.output_dir_entry.config(state=entry_state)
        self.output_browse_button.config(state=entry_state)
        
        # 只显示当前模式对应的设置区
        self.dialogue_frame.pack_forget()
        self.uniform_frame.pack_forget()
        if dialogue_mode:
            self.uniform_frame.pack(fill=tk.X, pady=5, before=self.progress_frame)
            self.dialogue_frame.pack(fill=tk.X, pady=5, before=self.progress_frame)
        else:
            self.uniform_frame.pack(fill=tk.X, pady=5, before=self.progress_frame)
    
    def select_input_dir(self):
        directory = filedialog.askdirectory(title="选择导入文件夹")
        if directory:
            self.input_dir = directory
            self.input_dir_entry.delete(0, tk.END)
            self.input_dir_entry.insert(0, directory)
            
            if self.mode.get() == "dialogue":
                # 台词模式：输出到该目录内同名的 <目录名>-pc（如 03 -> 03\03-pc）
                suggested_output = pipeline.derive_output_dir(directory)
            else:
                # 均匀截图模式：沿用原有规则，输出到父目录下的同名_output文件夹
                parent_dir = os.path.dirname(directory)
                folder_name = os.path.basename(directory)
                suggested_output = os.path.join(parent_dir, f"{folder_name}_output")
            
            self.output_dir = suggested_output
            self.output_dir_entry.delete(0, tk.END)
            self.output_dir_entry.insert(0, suggested_output)
    
    def select_output_dir(self):
        directory = filedialog.askdirectory(title="选择导出文件夹")
        if directory:
            self.output_dir = directory
            self.output_dir_entry.delete(0, tk.END)
            self.output_dir_entry.insert(0, directory)
    
    def update_progress(self, value):
        self.progress['value'] = value
        self.root.update_idletasks()
    
    def update_status(self, text):
        self.status_label.config(text=text)
        self.root.update_idletasks()
    
    def cancel_processing(self):
        """请求取消当前处理：台词模式会在每句之间检查并停止"""
        self.cancel_requested = True
        self.update_status("正在取消，请等待当前句处理完成…")
        self.cancel_button.config(state=tk.DISABLED)
    
    def start_processing(self):
        if self.processing:
            return
        
        # 检查输入文件夹
        if not self.input_dir or not os.path.isdir(self.input_dir):
            messagebox.showerror("错误", "请选择有效的导入文件夹")
            return
        
        dialogue_mode = self.mode.get() == "dialogue"
        options = None
        
        if dialogue_mode:
            # 台词模式的输出目录由「视频所在目录名 + -pc」自动推导，无需手工指定
            options = pipeline.DialogueOptions(
                subtitle_track=self.subtitle_track.get(),
                candidate_max=max(tool_config.CANDIDATE_MIN, self.candidate_max.get()),
                burn_subtitle=self.burn_subtitle.get(),
                max_cues=max(0, self.max_cues.get()),
                clean=self.clean_output.get(),
                eye_refine=self.eye_refine.get(),
                story_gaps=self.story_gaps.get(),
                story_gap_min=max(1.0, self.story_gap_min.get()),
                story_gap_max_per=max(1, self.story_gap_max_per.get()),
            )
        else:
            if not self.output_dir:
                messagebox.showerror("错误", "请选择有效的导出文件夹")
                return
            
            # 创建输出文件夹
            os.makedirs(self.output_dir, exist_ok=True)
            
            # 获取帧数
            num_frames = self.num_frames.get()
            if num_frames <= 0:
                messagebox.showerror("错误", "截取数量必须大于0")
                return
        
        # 禁用开始按钮，启用取消按钮
        self.processing = True
        self.cancel_requested = False
        self.start_button.config(state=tk.DISABLED)
        self.cancel_button.config(state=tk.NORMAL)
        self.update_progress(0)
        
        # 在新线程中处理视频
        if dialogue_mode:
            threading.Thread(target=self.process_dialogue, args=(self.input_dir, options), daemon=True).start()
        else:
            threading.Thread(target=self.process_videos, args=(self.input_dir, self.output_dir, self.num_frames.get()), daemon=True).start()
    
    def finish_processing(self):
        """恢复按钮可用状态"""
        self.processing = False
        self.cancel_requested = False
        self.start_button.config(state=tk.NORMAL)
        self.cancel_button.config(state=tk.DISABLED)
    
    def process_dialogue(self, input_dir, options):
        """台词智能截图模式：在后台线程中执行完整流程"""
        try:
            reporter = GuiReporter(self)
            result = pipeline.run(input_dir, options, reporter)
            
            lines = pipeline.summarize(result)
            self.update_status(lines[-1] if lines else "处理完成")
            messagebox.showinfo("完成", "\n".join(lines))
        except Exception as e:
            self.update_status(f"处理出错: {str(e)}")
            messagebox.showerror("错误", f"处理视频时出错:\n{str(e)}")
        finally:
            self.finish_processing()
    
    def find_all_videos(self, input_dir, base_input_dir, base_output_dir, current_depth=0, max_depth=10):
        """递归查找所有视频文件"""
        video_files = []        
        # 更新状态显示当前正在搜索的目录
        self.update_status(f"正在搜索目录: {input_dir} (深度: {current_depth}/{max_depth})")
        
        # 检查是否达到最大搜索深度
        if current_depth > max_depth:
            self.update_status(f"已达到最大搜索深度 {max_depth}，跳过目录: {input_dir}")
            return video_files
        
        # 获取当前目录下的所有文件和文件夹
        try:
            items = os.listdir(input_dir)
        except Exception as e:
            self.update_status(f"无法访问目录 {input_dir}: {str(e)}")
            return video_files
            
        # 处理当前目录中的文件
        for item in items:
            item_path = os.path.join(input_dir, item)
            
            # 如果是文件，检查是否为视频
            if os.path.isfile(item_path):
                try:
                    if is_video_file(item_path):
                        # 计算相对路径，用于在输出目录中创建相同的结构
                        rel_path = os.path.relpath(input_dir, base_input_dir)
                        output_subdir = os.path.join(base_output_dir, rel_path)
                        video_files.append((item_path, output_subdir))
                        self.update_status(f"找到视频文件: {os.path.basename(item_path)}")
                except Exception as e:
                    self.update_status(f"检查文件 {item_path} 时出错: {str(e)}")
                    continue
            
            # 如果是目录，递归处理
            elif os.path.isdir(item_path):
                try:
                    # 检查是否为符号链接
                    if not os.path.islink(item_path):
                        video_files.extend(self.find_all_videos(item_path, base_input_dir, base_output_dir, current_depth + 1, max_depth))
                except Exception as e:
                    self.update_status(f"处理目录 {item_path} 时出错: {str(e)}")
                    continue
                
        return video_files
    
    def process_videos(self, input_dir, output_dir, num_frames):
        try:
            self.update_status("正在递归搜索视频文件...")
            
            # 递归查找所有视频文件，并获取对应的输出目录
            video_files = self.find_all_videos(input_dir, input_dir, output_dir, 0, 10)
            
            if not video_files:
                self.update_status("未找到视频文件")
                messagebox.showinfo("信息", "在选定的文件夹及其子文件夹中未找到视频文件")
                self.finish_processing()
                return
            
            # 处理每个视频文件
            total_videos = len(video_files)
            for i, (video_path, output_subdir) in enumerate(video_files):
                video_name = os.path.basename(video_path)
                self.update_status(f"正在处理 {i+1}/{total_videos}: {video_name}")
                
                # 为每个视频创建一个子文件夹
                video_output_dir = os.path.join(output_subdir, os.path.splitext(os.path.basename(video_path))[0])
                
                # 重置进度条
                self.update_progress(0)
                
                # 提取帧
                actual_frames, success = extract_frames(video_path, video_output_dir, num_frames, self.update_progress)
                
                # 如果帧数被调整，显示提示信息
                if not success:
                    self.update_status(f"视频 {video_name} 太短，已自动调整截图数量为 {actual_frames} 张")
                    # 使用调整后的帧数继续处理
                    extract_frames(video_path, video_output_dir, actual_frames, self.update_progress)
            
            self.update_status("处理完成")
            messagebox.showinfo("完成", f"已成功处理 {total_videos} 个视频文件")
            
        except Exception as e:
            self.update_status(f"处理出错: {str(e)}")
            messagebox.showerror("错误", f"处理视频时出错:\n{str(e)}")
        
        finally:
            # 恢复开始按钮
            self.finish_processing()


def build_arg_parser():
    """命令行参数：便于逐集批量运行，无需打开界面"""
    parser = argparse.ArgumentParser(
        description="视频截图工具：台词智能截图（按内嵌字幕时间轴）与均匀截图",
    )
    parser.add_argument("--input", help="输入目录（台词模式建议传单集目录，如 ...\\Love in the big City\\03）")
    parser.add_argument("--mode", choices=["dialogue", "uniform"], default="dialogue",
                        help="截图模式，默认 dialogue（台词智能截图）")
    parser.add_argument("--output", help="均匀截图模式下的输出目录")
    parser.add_argument("--frames", type=int, default=8, help="均匀截图模式下的截取数量，默认 8")
    parser.add_argument("--subtitle-track", default="auto", help="字幕轨选择，默认 auto（自动优先简体中文）")
    parser.add_argument("--candidate-max", type=int, default=tool_config.CANDIDATE_MAX,
                        help="每句最多采样多少候选帧，默认 %d" % tool_config.CANDIDATE_MAX)
    parser.add_argument("--start-cue", type=int, default=1, help="从第几句开始处理，默认 1")
    parser.add_argument("--max-cues", type=int, default=0, help="只处理前几句，0 表示全部（默认）")
    parser.add_argument("--no-subtitle-text", action="store_true", help="不在图片上渲染台词文字")
    parser.add_argument("--no-resume", action="store_true", help="重新生成，不跳过已存在的图片")
    parser.add_argument("--clean", action="store_true",
                        help="重建：先清空输出目录中该集的历史图片与旧索引再生成")
    parser.add_argument("--eye-refine", choices=list(tool_config.EYE_REFINE_MODES), default="closed",
                        help="闭眼帧重采样口径：closed=整句都闭眼时重采样（默认），"
                             "half=半闭也重采样，off=关闭")
    parser.add_argument("--no-story-gaps", action="store_true",
                        help="不补截剧情延续帧（台词之间的无台词空窗）；默认补截")
    parser.add_argument("--story-gap-min", type=float, default=tool_config.STORY_GAP_MIN,
                        help="无台词空窗的最短间隔秒数（含片头/片尾），默认 %.1f"
                             % tool_config.STORY_GAP_MIN)
    parser.add_argument("--story-gap-max-per", type=int, default=tool_config.STORY_GAP_MAX_PER_GAP,
                        help="镜头去重后每个空窗最多补截几张，默认 %d"
                             % tool_config.STORY_GAP_MAX_PER_GAP)
    return parser


def run_cli(cli_args):
    """命令行模式入口，返回进程退出码"""
    if not cli_args.input or not os.path.isdir(cli_args.input):
        print("错误：请用 --input 指定有效的输入目录")
        return 1
    
    if cli_args.mode == "dialogue":
        options = pipeline.DialogueOptions(
            subtitle_track=cli_args.subtitle_track,
            candidate_max=max(tool_config.CANDIDATE_MIN, cli_args.candidate_max),
            burn_subtitle=not cli_args.no_subtitle_text,
            start_cue=max(1, cli_args.start_cue),
            max_cues=max(0, cli_args.max_cues),
            resume=not cli_args.no_resume,
            clean=cli_args.clean,
            eye_refine=cli_args.eye_refine,
            story_gaps=not cli_args.no_story_gaps,
            story_gap_min=max(1.0, cli_args.story_gap_min),
            story_gap_max_per=max(1, cli_args.story_gap_max_per),
        )
        try:
            result = pipeline.run(cli_args.input, options)
        except tool_config.VideoToolError as e:
            print("错误：%s" % e)
            return 1
        
        print()
        print("\n".join(pipeline.summarize(result)))
        return 0 if result.failed == 0 else 2
    
    # 均匀截图模式（沿用原有逻辑，输出到输入目录同级的 <目录名>_output）
    input_dir = os.path.abspath(cli_args.input)
    output_dir = cli_args.output or os.path.join(
        os.path.dirname(input_dir), os.path.basename(input_dir) + "_output"
    )
    
    videos = []
    for current_dir, _dir_names, file_names in os.walk(input_dir):
        for file_name in file_names:
            path = os.path.join(current_dir, file_name)
            if ffmpeg_utils.is_video_file(path):
                videos.append(path)
    
    if not videos:
        print("未找到视频文件")
        return 1
    
    for video_path in videos:
        target_dir = os.path.join(output_dir, os.path.splitext(os.path.basename(video_path))[0])
        print("正在处理：%s" % os.path.basename(video_path))
        extract_frames(video_path, target_dir, cli_args.frames)
    
    print("完成，共处理 %d 个视频，输出目录：%s" % (len(videos), output_dir))
    return 0


if __name__ == "__main__":
    args = build_arg_parser().parse_args()
    
    # 指定了 --input 就走命令行模式，否则打开图形界面
    if args.input:
        sys.exit(run_cli(args))
    
    root = tk.Tk()
    app = VideoFrameExtractorApp(root)
    root.mainloop()
