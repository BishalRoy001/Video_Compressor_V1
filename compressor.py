import os
import json
import queue
import shutil
import subprocess
import threading
import time
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import timedelta

class VideoCompressorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Smart Video Compressor (Portable Edition)")
        self.root.geometry("820x760")
        self.root.minsize(760, 680)

        # Worker & state variables
        self.log_queue = queue.Queue()
        self.process = None
        self.worker_thread = None
        self.is_running = False
        self.video_duration_sec = 0.0
        self.source_bitrate_kbps = 0
        self.start_time = None

        self._setup_styles()
        self._build_ui()
        self._check_system_dependencies()

        # Start periodic GUI queue consumer
        self.root.after(100, self._process_queue)

    def _get_engine_path(self, engine_name):
        """Finds ffmpeg/ffprobe right next to the .exe, otherwise defaults to system PATH"""
        if getattr(sys, 'frozen', False):
            # If running as a compiled .exe
            base_path = os.path.dirname(sys.executable)
        else:
            # If running as a normal Python script
            base_path = os.path.dirname(os.path.abspath(__file__))
            
        # Append .exe on Windows if not provided
        if os.name == 'nt' and not engine_name.endswith('.exe'):
            engine_name += '.exe'
            
        local_engine = os.path.join(base_path, engine_name)
        
        # If the .exe is next to the app, use it!
        if os.path.exists(local_engine):
            return local_engine
            
        # Otherwise, hope they have it installed on their system
        return engine_name

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TLabel", font=("Segoe UI", 9))
        style.configure("TLabelframe", font=("Segoe UI", 9, "bold"))
        style.configure("TLabelframe.Label", font=("Segoe UI", 9, "bold"))
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"), padding=6)
        style.configure("Danger.TButton", font=("Segoe UI", 10, "bold"), padding=6)
        style.configure("Horizontal.TProgressbar", thickness=18)

    def _build_ui(self):
        main_frame = ttk.Frame(self.root, padding=12)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 1. Source File Frame
        file_frame = ttk.LabelFrame(main_frame, text=" 1. Source Video Analysis ", padding=10)
        file_frame.pack(fill=tk.X, pady=(0, 10))

        file_row = ttk.Frame(file_frame)
        file_row.pack(fill=tk.X)

        self.path_var = tk.StringVar(value="No file selected...")
        ttk.Entry(file_row, textvariable=self.path_var, state="readonly").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        ttk.Button(file_row, text="Browse Video", command=self._browse_file).pack(side=tk.RIGHT)

        # Metadata cards
        self.meta_frame = ttk.Frame(file_frame, padding=(0, 8, 0, 0))
        self.meta_frame.pack(fill=tk.X)

        self.lbl_orig_size = ttk.Label(self.meta_frame, text="Size: --", foreground="#555")
        self.lbl_orig_size.pack(side=tk.LEFT, padx=(0, 16))

        self.lbl_duration = ttk.Label(self.meta_frame, text="Duration: --", foreground="#555")
        self.lbl_duration.pack(side=tk.LEFT, padx=(0, 16))
        
        self.lbl_bitrate = ttk.Label(self.meta_frame, text="Est. Bitrate: --", foreground="#555")
        self.lbl_bitrate.pack(side=tk.LEFT, padx=(0, 16))

        self.lbl_codec = ttk.Label(self.meta_frame, text="Codec: --", foreground="#555")
        self.lbl_codec.pack(side=tk.LEFT, padx=(0, 16))

        self.lbl_resolution = ttk.Label(self.meta_frame, text="Resolution: --", foreground="#555")
        self.lbl_resolution.pack(side=tk.LEFT)

        # 2. Compression Configuration Frame
        conf_frame = ttk.LabelFrame(main_frame, text=" 2. Guaranteed Reduction Settings ", padding=10)
        conf_frame.pack(fill=tk.X, pady=(0, 10))
        conf_frame.columnconfigure(1, weight=1)

        # Encoder selection
        ttk.Label(conf_frame, text="Encoder:").grid(row=0, column=0, sticky=tk.W, pady=4)
        self.encoder_var = tk.StringVar(value="GPU NVENC (H.265 / HEVC) - FAST")
        encoders = [
            "GPU NVENC (H.265 / HEVC) - FAST", 
            "GPU NVENC (H.264) - FAST",
            "CPU libx265 (HEVC) - SLOW, BEST QUALITY", 
            "CPU libx264 - SLOW"
        ]
        cb_encoder = ttk.Combobox(conf_frame, textvariable=self.encoder_var, values=encoders, state="readonly")
        cb_encoder.grid(row=0, column=1, sticky=tk.EW, padx=8, pady=4)

        # Percentage Slider
        ttk.Label(conf_frame, text="Target File Size:").grid(row=1, column=0, sticky=tk.W, pady=4)
        slider_frame = ttk.Frame(conf_frame)
        slider_frame.grid(row=1, column=1, sticky=tk.EW, padx=8, pady=4)

        self.pct_var = tk.IntVar(value=60)
        self.pct_scale = ttk.Scale(slider_frame, from_=10, to=95, variable=self.pct_var, command=self._update_pct_label)
        self.pct_scale.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.pct_val_lbl = ttk.Label(slider_frame, text="60% of Original Size", width=35)
        self.pct_val_lbl.pack(side=tk.RIGHT, padx=(8, 0))

        # Preset & Audio
        misc_opts = ttk.Frame(conf_frame)
        misc_opts.grid(row=2, column=0, columnspan=2, sticky=tk.EW, pady=6)

        ttk.Label(misc_opts, text="Preset:").pack(side=tk.LEFT)
        self.preset_var = tk.StringVar(value="slow")
        ttk.Combobox(misc_opts, textvariable=self.preset_var, values=["fast", "medium", "slow", "slower"], state="readonly", width=9).pack(side=tk.LEFT, padx=(4, 20))

        ttk.Label(misc_opts, text="Audio:").pack(side=tk.LEFT)
        self.audio_var = tk.StringVar(value="128k (AAC)")
        ttk.Combobox(misc_opts, textvariable=self.audio_var, values=["96k (Low)", "128k (AAC)", "160k (HQ)"], state="readonly", width=12).pack(side=tk.LEFT, padx=(4, 0))

        # 3. Execution Control & Live Metrics
        exec_frame = ttk.LabelFrame(main_frame, text=" 3. Execution Pipeline & Live Metrics ", padding=10)
        exec_frame.pack(fill=tk.X, pady=(0, 10))

        btn_row = ttk.Frame(exec_frame)
        btn_row.pack(fill=tk.X, pady=(0, 6))

        self.btn_start = ttk.Button(btn_row, text="Start Compression Job", style="Primary.TButton", command=self._start_compression)
        self.btn_start.pack(side=tk.LEFT, padx=(0, 10))

        self.btn_cancel = ttk.Button(btn_row, text="Halt Process", style="Danger.TButton", command=self._cancel_compression, state=tk.DISABLED)
        self.btn_cancel.pack(side=tk.LEFT)

        self.prog_var = tk.DoubleVar(value=0.0)
        self.progress_bar = ttk.Progressbar(exec_frame, variable=self.prog_var, maximum=100.0, style="Horizontal.TProgressbar")
        self.progress_bar.pack(fill=tk.X, pady=6)

        metrics_row = ttk.Frame(exec_frame)
        metrics_row.pack(fill=tk.X)
        self.lbl_pct = ttk.Label(metrics_row, text="Progress: 0.0%", font=("Segoe UI", 9, "bold"))
        self.lbl_pct.pack(side=tk.LEFT, padx=(0, 16))
        self.lbl_speed = ttk.Label(metrics_row, text="Speed: --", foreground="#2a6f97")
        self.lbl_speed.pack(side=tk.LEFT, padx=(0, 16))
        self.lbl_elapsed = ttk.Label(metrics_row, text="Elapsed: 00:00:00")
        self.lbl_elapsed.pack(side=tk.LEFT, padx=(0, 16))
        self.lbl_eta = ttk.Label(metrics_row, text="ETA: Calculating...", foreground="#d90429")
        self.lbl_eta.pack(side=tk.LEFT)

        # 4. Logs
        log_frame = ttk.LabelFrame(main_frame, text=" Backend Workflow Stream & Engine Output ", padding=8)
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(log_frame, wrap=tk.NONE, bg="#121212", fg="#e0e0e0", insertbackground="white", font=("Consolas", 8), height=12)
        scroll_y = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        scroll_x = ttk.Scrollbar(log_frame, orient=tk.HORIZONTAL, command=self.log_text.xview)
        self.log_text.configure(xscrollcommand=scroll_x.set, yscrollcommand=scroll_y.set)

        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.log_text.tag_config("INFO", foreground="#4cc9f0")
        self.log_text.tag_config("SUCCESS", foreground="#38b000")
        self.log_text.tag_config("WARN", foreground="#ffb703")
        self.log_text.tag_config("ERROR", foreground="#ef233c")

    def _check_system_dependencies(self):
        missing = []
        
        # Check if we have the local engines OR if they are in the system PATH
        ffmpeg_cmd = self._get_engine_path("ffmpeg")
        ffprobe_cmd = self._get_engine_path("ffprobe")
        
        if not os.path.exists(ffmpeg_cmd) and not shutil.which("ffmpeg"): 
            missing.append("ffmpeg")
        if not os.path.exists(ffprobe_cmd) and not shutil.which("ffprobe"): 
            missing.append("ffprobe")
            
        if missing:
            self._log(f"CRITICAL: Missing binaries: {', '.join(missing)}", "ERROR")
            self._log("Make sure ffmpeg.exe and ffprobe.exe are in the exact same folder as this application!", "ERROR")
            messagebox.showwarning("Missing Dependencies", "FFmpeg not found. Please place ffmpeg.exe and ffprobe.exe in the app folder.")
        else:
            self._log("System initialization complete. Engine discovered.", "SUCCESS")

    def _update_pct_label(self, val):
        pct = int(float(val))
        target_kbps = int(self.source_bitrate_kbps * (pct / 100.0)) if self.source_bitrate_kbps > 0 else 0
        desc = "Massive Loss" if pct <= 30 else "Balanced" if pct <= 70 else "High Quality"
        bitrate_str = f" (~{target_kbps} kbps)" if target_kbps > 0 else ""
        self.pct_val_lbl.config(text=f"{pct}% of Original Size{bitrate_str} [{desc}]")

    def _browse_file(self):
        file_path = filedialog.askopenfilename(title="Choose Video File", filetypes=[("Video Files", "*.mp4 *.mkv *.mov *.avi *.ts *.m4v")])
        if not file_path: return
        self.path_var.set(file_path)
        self._analyze_file(file_path)

    def _analyze_file(self, path):
        self._log(f"Analyzing source file: {os.path.basename(path)}", "INFO")
        size_bytes = os.path.getsize(path)
        size_gb = size_bytes / (1024 ** 3)
        self.lbl_orig_size.config(text=f"Size: {size_gb:.2f} GB ({size_bytes / (1024 ** 2):.0f} MB)")

        # Using the portable ffprobe path
        ffprobe_exe = self._get_engine_path("ffprobe")
        probe_cmd = [ffprobe_exe, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path]

        try:
            res = subprocess.run(probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
            info = json.loads(res.stdout)
            
            duration = float(info.get("format", {}).get("duration", 0.0))
            self.video_duration_sec = duration
            
            if duration > 0:
                total_kbps = (size_bytes * 8) / (duration * 1000)
                self.source_bitrate_kbps = max(100, int(total_kbps - 128))
            else:
                self.source_bitrate_kbps = 0
                
            self.lbl_duration.config(text=f"Duration: {str(timedelta(seconds=int(duration)))}")
            self.lbl_bitrate.config(text=f"Est. Bitrate: {self.source_bitrate_kbps} kbps")

            video_stream = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
            if video_stream:
                self.lbl_codec.config(text=f"Codec: {video_stream.get('codec_name', 'Unknown').upper()}")
                self.lbl_resolution.config(text=f"Resolution: {video_stream.get('width', '--')}x{video_stream.get('height', '--')}")
            
            self._update_pct_label(self.pct_var.get())
            self._log(f"Source confirmed. Engine limits will be based on {self.source_bitrate_kbps} kbps.", "INFO")
        except Exception as e:
            self._log(f"Probe analysis failed: {str(e)}", "WARN")

    def _log(self, message, tag=None):
        self.log_text.insert(tk.END, time.strftime("[%H:%M:%S] "))
        self.log_text.insert(tk.END, f"{message}\n", tag)
        self.log_text.see(tk.END)

    def _start_compression(self):
        input_path = self.path_var.get()
        if not os.path.exists(input_path) or self.source_bitrate_kbps == 0:
            messagebox.showerror("Error", "Please select a valid video file.")
            return

        out_dir = os.path.dirname(input_path)
        base, _ = os.path.splitext(os.path.basename(input_path))
        output_path = os.path.join(out_dir, f"{base}_compressed.mp4")

        pct = self.pct_scale.get()
        target_kbps = int(self.source_bitrate_kbps * (pct / 100.0))
        target_bitrate_str = f"{target_kbps}k"
        
        encoder_choice = self.encoder_var.get()
        preset_val = self.preset_var.get()
        audio_bitrate = self.audio_var.get().split()[0]

        # Using the portable ffmpeg path
        ffmpeg_exe = self._get_engine_path("ffmpeg")
        cmd = [ffmpeg_exe, "-y", "-nostdin", "-i", input_path]

        if "NVENC (H.265" in encoder_choice:
            cmd.extend(["-c:v", "hevc_nvenc", "-b:v", target_bitrate_str, "-maxrate", target_bitrate_str, "-preset", preset_val])
        elif "NVENC (H.264" in encoder_choice:
            cmd.extend(["-c:v", "h264_nvenc", "-b:v", target_bitrate_str, "-maxrate", target_bitrate_str, "-preset", preset_val])
        elif "libx265" in encoder_choice:
            cmd.extend(["-c:v", "libx265", "-b:v", target_bitrate_str, "-maxrate", target_bitrate_str, "-preset", preset_val])
        elif "libx264" in encoder_choice:
             cmd.extend(["-c:v", "libx264", "-b:v", target_bitrate_str, "-maxrate", target_bitrate_str, "-preset", preset_val])

        cmd.extend(["-c:a", "aac", "-b:a", audio_bitrate, "-progress", "pipe:1", "-nostats", output_path])

        self._log("----------------------------------------------------------------", "INFO")
        self._log(f"STRICT FILE SIZE REDUCTION ENGAGED.", "WARN")
        self._log(f"Forcing maximum video bitrate to: {target_kbps} kbps ({pct}% of original)", "INFO")
        
        self.btn_start.config(state=tk.DISABLED)
        self.btn_cancel.config(state=tk.NORMAL)
        self.is_running = True
        self.start_time = time.time()
        self.prog_var.set(0.0)

        self.worker_thread = threading.Thread(target=self._run_ffmpeg_pipeline, args=(cmd, output_path), daemon=True)
        self.worker_thread.start()

    def _run_ffmpeg_pipeline(self, cmd, output_path):
        try:
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, startupinfo=startupinfo, bufsize=1)
            for line in self.process.stdout:
                line_str = line.strip()
                if line_str: self.log_queue.put(("PROGRESS_PARSE", line_str))

            self.process.wait()
            if self.process.returncode == 0: self.log_queue.put(("DONE", output_path))
            else: self.log_queue.put(("ABORTED", f"Exited with code {self.process.returncode}"))
        except Exception as e:
            self.log_queue.put(("ERROR", str(e)))

    def _process_queue(self):
        try:
            # GUI THROTTLE: Process a max of 50 items per UI tick to prevent freezing on massive files
            count = 0
            while count < 50:
                msg_type, data = self.log_queue.get_nowait()
                if msg_type == "PROGRESS_PARSE": self._parse_ffmpeg_line(data)
                elif msg_type == "DONE": self._on_complete(data)
                elif msg_type == "ABORTED": self._on_terminated(data)
                elif msg_type == "ERROR": 
                    self._log(f"Execution failed: {data}", "ERROR")
                    self._reset_ui_state()
                count += 1
        except queue.Empty: pass

        if self.is_running and self.start_time:
            self.lbl_elapsed.config(text=f"Elapsed: {str(timedelta(seconds=int(time.time() - self.start_time)))}")

        self.root.after(100, self._process_queue)

    def _parse_ffmpeg_line(self, line):
        if "=" not in line:
            self._log(line)
            return
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()

        # SPAM FILTER: Hide highly repetitive math from the log window
        spam_keys = ("frame", "fps", "bitrate", "speed", "progress", "out_time_us", "out_time_ms", "out_time", "dup_frames", "drop_frames", "stream_0_0_q", "total_size")
        
        if key not in spam_keys: 
            self._log(line)

        # Update progress bar math
        if key == "out_time_us" and self.video_duration_sec > 0:
            try:
                pct = min(100.0, ((float(val) / 1_000_000.0) / self.video_duration_sec) * 100.0)
                self.prog_var.set(pct)
                self.lbl_pct.config(text=f"Progress: {pct:.1f}%")
                if self.start_time and pct > 0.5:
                    elapsed = time.time() - self.start_time
                    self.lbl_eta.config(text=f"ETA: {str(timedelta(seconds=max(0, int((elapsed / (pct / 100.0)) - elapsed))))}")
            except ValueError: pass
        elif key == "speed": self.lbl_speed.config(text=f"Speed: {val}")

    def _cancel_compression(self):
        if self.process and self.is_running:
            self._log("Stopping FFmpeg...", "WARN")
            self.process.terminate()
            self._reset_ui_state()

    def _on_complete(self, output_path):
        self.prog_var.set(100.0)
        self.lbl_pct.config(text="Progress: 100.0%")
        self.lbl_eta.config(text="ETA: Completed")

        orig_mb = os.path.getsize(self.path_var.get()) / (1024 ** 2)
        new_mb = os.path.getsize(output_path) / (1024 ** 2)
        reduction = (1.0 - (new_mb / orig_mb)) * 100.0

        self._log("JOB COMPLETED SUCCESSFULLY!", "SUCCESS")
        self._log(f"Original: {orig_mb:.1f} MB -> Final: {new_mb:.1f} MB ({reduction:.1f}% space saved)", "SUCCESS")
        self._reset_ui_state()
        messagebox.showinfo("Success", f"Original: {orig_mb:.1f} MB\nCompressed: {new_mb:.1f} MB\nSaved: {reduction:.1f}%")

    def _on_terminated(self, reason):
        self._log(f"Job halted: {reason}", "WARN")
        self._reset_ui_state()

    def _reset_ui_state(self):
        self.is_running = False
        self.btn_start.config(state=tk.NORMAL)
        self.btn_cancel.config(state=tk.DISABLED)

if __name__ == "__main__":
    root = tk.Tk()
    app = VideoCompressorApp(root)
    root.mainloop()