import argparse
import subprocess
import os
import sys
import tkinter as tk
from tkinter import filedialog, ttk, messagebox
import threading

# Feature package: subtitle parse, candidate scoring, dialogue burn-in, pipeline
from video_tool import config as tool_config
from video_tool import ffmpeg_utils, pipeline


def is_video_file(file_path):
    """Return True if path looks like a video file."""
    # Implemented in video_tool.ffmpeg_utils (locates ffmpeg without relying on PATH)
    return ffmpeg_utils.is_video_file(file_path)

def extract_frames(video_path, output_dir, num_frames, progress_callback=None):
    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    # Get video duration via FFmpeg
    ffprobe_cmd = [ffmpeg_utils.ffprobe_path(), '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', video_path]
    duration = float(subprocess.check_output(ffprobe_cmd).decode().strip())

    # Get frame rate
    ffprobe_fps_cmd = [ffmpeg_utils.ffprobe_path(), '-v', 'error', '-select_streams', 'v', '-show_entries', 'stream=r_frame_rate', '-of', 'default=noprint_wrappers=1:nokey=1', video_path]
    fps_str = subprocess.check_output(ffprobe_fps_cmd).decode().strip()
    
    # Parse frame rate (may be "24/1")
    if '/' in fps_str:
        num, den = map(int, fps_str.split('/'))
        fps = num / den if den != 0 else 0
    else:
        fps = float(fps_str) if fps_str else 0
    
    # Estimate total frames
    total_frames = int(duration * fps) if fps > 0 else 0
    
    # Cap frame count at 80% of total frames
    if total_frames > 0 and num_frames > total_frames * 0.8:
        adjusted_num_frames = max(1, int(total_frames * 0.8))
        if progress_callback:
            progress_callback(0)  # reset progress bar
        return adjusted_num_frames, False  # adjusted count; caller should retry
    
    # Time interval (keep a minimum)
    min_interval = 0.1  # seconds
    if duration <= num_frames * min_interval:
        # Video too short; widen interval
        interval = max(min_interval, duration / (num_frames + 1))
    else:
        interval = duration / (num_frames + 1)
    
    # Extract frames
    try:
        for i in range(1, num_frames + 1):
            time_pos = i * interval
            minutes = int(time_pos // 60)
            seconds = int(time_pos % 60)
            milliseconds = int((time_pos % 1) * 1000)
            output_file = os.path.join(output_dir, f'{os.path.splitext(os.path.basename(video_path))[0]}_time_{minutes:02d}m{seconds:02d}s{milliseconds:03d}.jpg')
            
            # Timeout guard
            ffmpeg_cmd = [ffmpeg_utils.ffmpeg_path(), '-ss', str(time_pos), '-i', video_path, '-vframes', '1', '-q:v', '2', output_file]
            try:
                # 10 second timeout
                subprocess.run(ffmpeg_cmd, timeout=10)
            except subprocess.TimeoutExpired:
                print(f"Frame {i} timed out, skipping")
                continue
            
            # Update progress
            if progress_callback:
                progress_callback(i / num_frames * 100)
    except Exception as e:
        print(f"Error extracting frames: {str(e)}")
        raise
    
    return num_frames, True  # actual count and success flag


HELP_TEXT_UNIFORM = """How to use (uniform frames):
1. Click "Browse..." to choose a folder that contains video files
2. The output folder is set automatically; you can change it with "Browse..."
3. Set how many frames to capture per video (default: 8)
4. Click "Start" to batch-process videos

Notes:
· The tool recursively searches the selected folder for video files
· Frames are spaced evenly across each video's duration
· For short videos, the frame count is adjusted automatically
· Watch the progress bar while processing
· FFmpeg is required"""

HELP_TEXT_DIALOGUE = """How to use (dialogue smart capture):
1. Click "Browse..." to choose the folder for one episode (e.g. ...\\your-show\\03)
2. Output is auto-set to 03-pics inside that folder; no manual path needed
3. Adjust subtitle track, candidates per line, and other options as needed
4. Click "Start"; the UI shows progress as "line N/M"

How it works:
· Reads embedded soft subtitles (prefers Simplified Chinese) for each line's start/end
· Samples multiple candidate frames in each line's time range, scores them on sharpness,
  motion, face naturalness (eyes open, mouth speaking), and context fit (centered
  composition, near mid-cue, same shot as mid-cue), then keeps only the top frame
· Burns the line at the bottom in bold white with black outline (no background);
  font size, outline width, and bottom inset come from style_profile.json baselines
· Empty shots, backs-of-heads, and title cards (including black frames) still export,
  marked as degraded when applicable
· If no candidate has open eyes, auto-resamples once with more candidates
· Gaps between lines with no dialogue (≥ 6 s, including open/close) get "story
  continuation" frames: reaction shots, object close-ups, flashbacks, etc.
  Per gap: dedupe by shot, keep the best up to 3; cue ids are previous-line 4-digit
  index + a/b/c (e.g. 0004a, 0298b) so filenames sort between dialogue stills;
  rows go into _index.csv; review keep/drop manually — deleted rows are not
  re-added on re-run (index keeps the ledger)
· Final integrity check (subtitle count / image count / index rows); missing lines re-run
· Writes _index.csv (dialogue index) and _report.txt (run report) in the output folder
· Image names like S01E03_0007_00-12-34.567.jpg, ordered by cue and timecode

For the next episode, point input at 04 (output becomes 04\\04-pics)."""


class GuiReporter(pipeline.Reporter):
    """Forward pipeline progress/status callbacks to the tkinter UI."""

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
        
        # Capture mode: dialogue = smart dialogue stills; uniform = even spacing
        self.mode = tk.StringVar(value="dialogue")
        # Dialogue-mode options
        self.subtitle_track = tk.StringVar(value="auto")
        self.candidate_max = tk.IntVar(value=tool_config.CANDIDATE_MAX)
        self.max_cues = tk.IntVar(value=0)
        self.burn_subtitle = tk.BooleanVar(value=True)
        # Rebuild: clear prior images/index in this episode's output first
        self.clean_output = tk.BooleanVar(value=False)
        # Eye refine: if no open-eye candidate, bump candidates and retry
        self.eye_refine = tk.StringVar(value="closed")
        # Story gaps: fill no-dialogue windows between lines
        self.story_gaps = tk.BooleanVar(value=True)
        self.story_gap_min = tk.DoubleVar(value=tool_config.STORY_GAP_MIN)
        self.story_gap_max_per = tk.IntVar(value=tool_config.STORY_GAP_MAX_PER_GAP)
        
        self.create_widgets()
    
    def create_widgets(self):
        # Main frame
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Help section
        help_frame = ttk.LabelFrame(main_frame, text="Instructions", padding="5")
        help_frame.pack(fill=tk.X, pady=5)
        
        self.help_text_var = tk.StringVar(value=HELP_TEXT_DIALOGUE)
        help_label = ttk.Label(help_frame, textvariable=self.help_text_var, justify=tk.LEFT, wraplength=720)
        help_label.pack(fill=tk.X, padx=5, pady=5)
        
        # Mode selection
        mode_frame = ttk.LabelFrame(main_frame, text="Capture mode", padding="5")
        mode_frame.pack(fill=tk.X, pady=5)
        
        ttk.Radiobutton(mode_frame, text="Dialogue smart capture (pick best frame per embedded subtitle line)",
                        variable=self.mode, value="dialogue",
                        command=self.on_mode_changed).grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        ttk.Radiobutton(mode_frame, text="Uniform frames (original mode: evenly spaced across duration)",
                        variable=self.mode, value="uniform",
                        command=self.on_mode_changed).grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        
        # Input folder
        input_frame = ttk.LabelFrame(main_frame, text="Input", padding="5")
        input_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(input_frame, text="Import folder:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.input_dir_entry = ttk.Entry(input_frame, width=50)
        self.input_dir_entry.grid(row=0, column=1, sticky=tk.W+tk.E, padx=5, pady=5)
        ttk.Button(input_frame, text="Browse...", command=self.select_input_dir).grid(row=0, column=2, padx=5, pady=5)
        
        # Output folder
        self.output_frame = ttk.LabelFrame(main_frame, text="Output", padding="5")
        self.output_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(self.output_frame, text="Export folder:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.output_dir_entry = ttk.Entry(self.output_frame, width=50)
        self.output_dir_entry.grid(row=0, column=1, sticky=tk.W+tk.E, padx=5, pady=5)
        self.output_browse_button = ttk.Button(self.output_frame, text="Browse...", command=self.select_output_dir)
        self.output_browse_button.grid(row=0, column=2, padx=5, pady=5)
        
        # Uniform-mode settings
        self.uniform_frame = ttk.LabelFrame(main_frame, text="Uniform frame settings", padding="5")
        self.uniform_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(self.uniform_frame, text="Frame count:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Spinbox(self.uniform_frame, from_=1, to=1000, textvariable=self.num_frames, width=10).grid(row=0, column=1, sticky=tk.W, padx=5, pady=5)
        
        # Dialogue-mode settings
        self.dialogue_frame = ttk.LabelFrame(main_frame, text="Dialogue capture settings", padding="5")
        self.dialogue_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(self.dialogue_frame, text="Subtitle track:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        # Values are track-name keywords used for matching; keep as-is
        ttk.Combobox(self.dialogue_frame, textvariable=self.subtitle_track, width=14, state="readonly",
                     values=("auto", "简体中文", "繁体中文")).grid(row=0, column=1, sticky=tk.W, padx=5, pady=5)
        ttk.Label(self.dialogue_frame, text="auto = auto-select, prefer Simplified Chinese").grid(row=0, column=2, sticky=tk.W, padx=5, pady=5)
        
        ttk.Label(self.dialogue_frame, text="Candidates per line:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Spinbox(self.dialogue_frame, from_=tool_config.CANDIDATE_MIN, to=30, textvariable=self.candidate_max,
                    width=10).grid(row=1, column=1, sticky=tk.W, padx=5, pady=5)
        ttk.Label(self.dialogue_frame, text="More candidates improve open-eye/sharp picks; slightly slower").grid(row=1, column=2, sticky=tk.W, padx=5, pady=5)
        
        ttk.Label(self.dialogue_frame, text="Only first N lines:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Spinbox(self.dialogue_frame, from_=0, to=100000, textvariable=self.max_cues, width=10).grid(row=2, column=1, sticky=tk.W, padx=5, pady=5)
        ttk.Label(self.dialogue_frame, text="0 = all; try 3 for a quick preview").grid(row=2, column=2, sticky=tk.W, padx=5, pady=5)
        
        ttk.Checkbutton(self.dialogue_frame, text="Burn dialogue text at bottom (white outline, no fill; style from reference baseline)",
                        variable=self.burn_subtitle).grid(row=3, column=0, columnspan=3, sticky=tk.W, padx=5, pady=5)
        
        ttk.Checkbutton(self.dialogue_frame, text="Clear output folder then rebuild (delete prior images/index for this episode)",
                        variable=self.clean_output).grid(row=4, column=0, columnspan=3, sticky=tk.W, padx=5, pady=5)
        
        ttk.Label(self.dialogue_frame, text="Closed-eye resample:").grid(row=5, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Combobox(self.dialogue_frame, textvariable=self.eye_refine, width=12, state="readonly",
                     values=tool_config.EYE_REFINE_MODES).grid(row=5, column=1, sticky=tk.W, padx=5, pady=5)
        ttk.Label(self.dialogue_frame, text="closed = only when all eyes closed; half = include half-closed; off = disabled").grid(
            row=5, column=2, sticky=tk.W, padx=5, pady=5)
        
        ttk.Checkbutton(self.dialogue_frame, text="Add story-continuation frames (no-dialogue gaps: reactions, objects, etc.; ids 0004a/b/c…; review manually)",
                        variable=self.story_gaps).grid(row=6, column=0, columnspan=3, sticky=tk.W, padx=5, pady=5)
        
        ttk.Label(self.dialogue_frame, text="Gap threshold (s):").grid(row=7, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Spinbox(self.dialogue_frame, from_=1.0, to=120.0, increment=0.5,
                    textvariable=self.story_gap_min, width=10).grid(row=7, column=1, sticky=tk.W, padx=5, pady=5)
        ttk.Label(self.dialogue_frame, text="Fill gaps when adjacent lines are at least this many seconds apart (incl. open/close)").grid(
            row=7, column=2, sticky=tk.W, padx=5, pady=5)
        
        ttk.Label(self.dialogue_frame, text="Max per gap:").grid(row=8, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Spinbox(self.dialogue_frame, from_=1, to=10, textvariable=self.story_gap_max_per,
                    width=10).grid(row=8, column=1, sticky=tk.W, padx=5, pady=5)
        ttk.Label(self.dialogue_frame, text="After shot dedupe, max frames per gap").grid(
            row=8, column=2, sticky=tk.W, padx=5, pady=5)
        
        # Progress
        self.progress_frame = ttk.LabelFrame(main_frame, text="Progress", padding="5")
        self.progress_frame.pack(fill=tk.X, pady=5)
        
        self.progress = ttk.Progressbar(self.progress_frame, orient=tk.HORIZONTAL, length=100, mode='determinate')
        self.progress.pack(fill=tk.X, padx=5, pady=5)
        
        self.status_label = ttk.Label(self.progress_frame, text="Ready")
        self.status_label.pack(anchor=tk.W, padx=5, pady=5)
        
        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=10)
        
        self.start_button = ttk.Button(button_frame, text="Start", command=self.start_processing)
        self.start_button.pack(side=tk.RIGHT, padx=5)
        
        self.cancel_button = ttk.Button(button_frame, text="Cancel", command=self.cancel_processing, state=tk.DISABLED)
        self.cancel_button.pack(side=tk.RIGHT, padx=5)
        
        # Copyright
        copyright_label = ttk.Label(main_frame, text=tool_config.COPYRIGHT, font=("Arial", 9))
        copyright_label.pack(side=tk.BOTTOM, pady=5)
        
        # Sync UI to current mode
        self.on_mode_changed()
    
    def on_mode_changed(self):
        """Update help text and enable/disable mode-specific controls."""
        dialogue_mode = self.mode.get() == "dialogue"
        
        self.help_text_var.set(HELP_TEXT_DIALOGUE if dialogue_mode else HELP_TEXT_UNIFORM)
        
        # Dialogue mode derives output as <dirname>-pics; disable manual path
        entry_state = tk.DISABLED if dialogue_mode else tk.NORMAL
        self.output_dir_entry.config(state=entry_state)
        self.output_browse_button.config(state=entry_state)
        
        # Show only the settings for the active mode
        self.dialogue_frame.pack_forget()
        self.uniform_frame.pack_forget()
        if dialogue_mode:
            self.uniform_frame.pack(fill=tk.X, pady=5, before=self.progress_frame)
            self.dialogue_frame.pack(fill=tk.X, pady=5, before=self.progress_frame)
        else:
            self.uniform_frame.pack(fill=tk.X, pady=5, before=self.progress_frame)
    
    def select_input_dir(self):
        directory = filedialog.askdirectory(title="Select import folder")
        if directory:
            self.input_dir = directory
            self.input_dir_entry.delete(0, tk.END)
            self.input_dir_entry.insert(0, directory)
            
            if self.mode.get() == "dialogue":
                # Dialogue: <dirname>-pics under the episode folder (e.g. 03 -> 03\03-pics)
                suggested_output = pipeline.derive_output_dir(directory)
            else:
                # Uniform: sibling <folder>_output under the parent
                parent_dir = os.path.dirname(directory)
                folder_name = os.path.basename(directory)
                suggested_output = os.path.join(parent_dir, f"{folder_name}_output")
            
            self.output_dir = suggested_output
            self.output_dir_entry.delete(0, tk.END)
            self.output_dir_entry.insert(0, suggested_output)
    
    def select_output_dir(self):
        directory = filedialog.askdirectory(title="Select export folder")
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
        """Request cancel; dialogue mode checks between cues."""
        self.cancel_requested = True
        self.update_status("Cancelling; waiting for current line to finish…")
        self.cancel_button.config(state=tk.DISABLED)
    
    def start_processing(self):
        if self.processing:
            return
        
        # Validate input folder
        if not self.input_dir or not os.path.isdir(self.input_dir):
            messagebox.showerror("Error", "Please select a valid import folder")
            return
        
        dialogue_mode = self.mode.get() == "dialogue"
        options = None
        
        if dialogue_mode:
            # Dialogue output is derived as <dirname>-pics; no manual path
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
                messagebox.showerror("Error", "Please select a valid export folder")
                return
            
            # Create output folder
            os.makedirs(self.output_dir, exist_ok=True)
            
            # Frame count
            num_frames = self.num_frames.get()
            if num_frames <= 0:
                messagebox.showerror("Error", "Frame count must be greater than 0")
                return
        
        # Disable Start, enable Cancel
        self.processing = True
        self.cancel_requested = False
        self.start_button.config(state=tk.DISABLED)
        self.cancel_button.config(state=tk.NORMAL)
        self.update_progress(0)
        
        # Process on a background thread
        if dialogue_mode:
            threading.Thread(target=self.process_dialogue, args=(self.input_dir, options), daemon=True).start()
        else:
            threading.Thread(target=self.process_videos, args=(self.input_dir, self.output_dir, self.num_frames.get()), daemon=True).start()
    
    def finish_processing(self):
        """Restore button states."""
        self.processing = False
        self.cancel_requested = False
        self.start_button.config(state=tk.NORMAL)
        self.cancel_button.config(state=tk.DISABLED)
    
    def process_dialogue(self, input_dir, options):
        """Dialogue smart capture: run full pipeline on a worker thread."""
        try:
            reporter = GuiReporter(self)
            result = pipeline.run(input_dir, options, reporter)
            
            lines = pipeline.summarize(result)
            self.update_status(lines[-1] if lines else "Done")
            messagebox.showinfo("Done", "\n".join(lines))
        except Exception as e:
            self.update_status(f"Error: {str(e)}")
            messagebox.showerror("Error", f"Error processing video:\n{str(e)}")
        finally:
            self.finish_processing()
    
    def find_all_videos(self, input_dir, base_input_dir, base_output_dir, current_depth=0, max_depth=10):
        """Recursively find all video files."""
        video_files = []        
        # Status: directory being searched
        self.update_status(f"Searching: {input_dir} (depth: {current_depth}/{max_depth})")
        
        # Max depth check
        if current_depth > max_depth:
            self.update_status(f"Max search depth {max_depth} reached, skipping: {input_dir}")
            return video_files
        
        # List entries
        try:
            items = os.listdir(input_dir)
        except Exception as e:
            self.update_status(f"Cannot access {input_dir}: {str(e)}")
            return video_files
            
        # Files in this directory
        for item in items:
            item_path = os.path.join(input_dir, item)
            
            # File: check if video
            if os.path.isfile(item_path):
                try:
                    if is_video_file(item_path):
                        # Mirror relative path under output
                        rel_path = os.path.relpath(input_dir, base_input_dir)
                        output_subdir = os.path.join(base_output_dir, rel_path)
                        video_files.append((item_path, output_subdir))
                        self.update_status(f"Found video: {os.path.basename(item_path)}")
                except Exception as e:
                    self.update_status(f"Error checking {item_path}: {str(e)}")
                    continue
            
            # Directory: recurse (skip symlinks)
            elif os.path.isdir(item_path):
                try:
                    if not os.path.islink(item_path):
                        video_files.extend(self.find_all_videos(item_path, base_input_dir, base_output_dir, current_depth + 1, max_depth))
                except Exception as e:
                    self.update_status(f"Error processing directory {item_path}: {str(e)}")
                    continue
                
        return video_files
    
    def process_videos(self, input_dir, output_dir, num_frames):
        try:
            self.update_status("Recursively searching for videos...")
            
            # Find all videos and their output dirs
            video_files = self.find_all_videos(input_dir, input_dir, output_dir, 0, 10)
            
            if not video_files:
                self.update_status("No video files found")
                messagebox.showinfo("Info", "No video files found in the selected folder or its subfolders")
                self.finish_processing()
                return
            
            # Process each video
            total_videos = len(video_files)
            for i, (video_path, output_subdir) in enumerate(video_files):
                video_name = os.path.basename(video_path)
                self.update_status(f"Processing {i+1}/{total_videos}: {video_name}")
                
                # Per-video subfolder
                video_output_dir = os.path.join(output_subdir, os.path.splitext(os.path.basename(video_path))[0])
                
                # Reset progress
                self.update_progress(0)
                
                # Extract frames
                actual_frames, success = extract_frames(video_path, video_output_dir, num_frames, self.update_progress)
                
                # If count was adjusted, retry with the new count
                if not success:
                    self.update_status(f"Video {video_name} is short; adjusted frame count to {actual_frames}")
                    extract_frames(video_path, video_output_dir, actual_frames, self.update_progress)
            
            self.update_status("Done")
            messagebox.showinfo("Done", f"Successfully processed {total_videos} video file(s)")
            
        except Exception as e:
            self.update_status(f"Error: {str(e)}")
            messagebox.showerror("Error", f"Error processing video:\n{str(e)}")
        
        finally:
            # Restore Start button
            self.finish_processing()


def build_arg_parser():
    """CLI args for batch runs without opening the GUI."""
    parser = argparse.ArgumentParser(
        description="Video stills tool: dialogue smart capture (embedded subtitles) and uniform frames",
    )
    parser.add_argument("--input", help="Input directory (dialogue mode: one episode folder, e.g. ...\\your-show\\03)")
    parser.add_argument("--mode", choices=["dialogue", "uniform"], default="dialogue",
                        help="Capture mode; default dialogue (smart dialogue stills)")
    parser.add_argument("--output", help="Output directory for uniform mode")
    parser.add_argument("--frames", type=int, default=8, help="Frame count in uniform mode; default 8")
    parser.add_argument("--subtitle-track", default="auto", help="Subtitle track; default auto (prefer Simplified Chinese)")
    parser.add_argument("--candidate-max", type=int, default=tool_config.CANDIDATE_MAX,
                        help="Max candidate frames per line; default %d" % tool_config.CANDIDATE_MAX)
    parser.add_argument("--start-cue", type=int, default=1, help="Start from this cue number; default 1")
    parser.add_argument("--max-cues", type=int, default=0, help="Only process first N cues; 0 = all (default)")
    parser.add_argument("--no-subtitle-text", action="store_true", help="Do not burn dialogue text onto images")
    parser.add_argument("--no-resume", action="store_true", help="Regenerate; do not skip existing images")
    parser.add_argument("--clean", action="store_true",
                        help="Rebuild: clear prior images/index in the output folder first")
    parser.add_argument("--eye-refine", choices=list(tool_config.EYE_REFINE_MODES), default="closed",
                        help="Closed-eye resample: closed=retry when all eyes closed (default), "
                             "half=also half-closed, off=disabled")
    parser.add_argument("--no-story-gaps", action="store_true",
                        help="Do not add story-continuation frames for no-dialogue gaps; default is to add them")
    parser.add_argument("--story-gap-min", type=float, default=tool_config.STORY_GAP_MIN,
                        help="Minimum no-dialogue gap length in seconds (incl. open/close); default %.1f"
                             % tool_config.STORY_GAP_MIN)
    parser.add_argument("--story-gap-max-per", type=int, default=tool_config.STORY_GAP_MAX_PER_GAP,
                        help="Max frames per gap after shot dedupe; default %d"
                             % tool_config.STORY_GAP_MAX_PER_GAP)
    return parser


def run_cli(cli_args):
    """CLI entry point; returns process exit code."""
    if not cli_args.input or not os.path.isdir(cli_args.input):
        print("Error: pass a valid input directory with --input")
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
            print("Error: %s" % e)
            return 1
        
        print()
        print("\n".join(pipeline.summarize(result)))
        return 0 if result.failed == 0 else 2
    
    # Uniform mode (sibling <dirname>_output under input's parent)
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
        print("No video files found")
        return 1
    
    for video_path in videos:
        target_dir = os.path.join(output_dir, os.path.splitext(os.path.basename(video_path))[0])
        print("Processing: %s" % os.path.basename(video_path))
        extract_frames(video_path, target_dir, cli_args.frames)
    
    print("Done; processed %d video(s); output: %s" % (len(videos), output_dir))
    return 0


if __name__ == "__main__":
    args = build_arg_parser().parse_args()
    
    # --input present -> CLI; otherwise open GUI
    if args.input:
        sys.exit(run_cli(args))
    
    root = tk.Tk()
    app = VideoFrameExtractorApp(root)
    root.mainloop()
