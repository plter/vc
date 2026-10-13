#!/usr/bin/env python3
"""音色转换图形界面 (vcui)。

main.py 转换流水线的窗体版本:
  1. ffmpeg 将输入音频转成标准 wav (24kHz / 单声道 / 16bit PCM)
  2. silencedetect 按停顿切分音频
  3. audiocpp_cli 对每个分段做音色转换
  4. ffmpeg concat 合并为输出文件

所有路径选项保存在 vcui_config.json (与本文件同目录), 下次启动自动恢复。

Usage: python vcui.py
"""

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

APP_TITLE = "音色转换工具"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "vcui_config.json")

# 与 main.py 保持一致的转换参数
SILENCE_NOISE = "-35dB"
SILENCE_MIN_DURATION = 0.6
MIN_SEGMENT_DURATION = 1.0
SAMPLE_RATE = 24000

SIL_START_RE = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
SIL_END_RE = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")

# Windows 下隐藏子进程控制台窗口
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

AUDIO_FILETYPES = [
    ("音频文件", "*.wav *.mp3 *.flac *.ogg *.m4a *.aac *.wma *.opus"),
    ("所有文件", "*.*"),
]
EXE_PATTERN = "*.exe" if os.name == "nt" else "*"


def detect_cpu_threads():
    if hasattr(os, "process_cpu_count"):
        return os.process_cpu_count()
    if hasattr(os, "sched_getaffinity"):
        return len(os.sched_getaffinity(0))
    return os.cpu_count() or 1


def default_config():
    """首次启动时的默认值: 尽量自动探测, 减少手工选择。"""
    ref_default = os.path.join(SCRIPT_DIR, "audioref", "audio.wav")
    return {
        "ffmpeg": shutil.which("ffmpeg") or "",
        "ffprobe": shutil.which("ffprobe") or "",
        "audiocpp": "",
        "model": "",
        "input": "",
        "ref": ref_default if os.path.isfile(ref_default) else "",
        "output": os.path.join(SCRIPT_DIR, "vc_out.wav"),
    }


class ConversionCancelled(Exception):
    pass


class Converter:
    """在后台线程中执行转换流水线, 通过回调输出日志。"""

    def __init__(self, cfg, log, should_cancel):
        self.ffmpeg = cfg["ffmpeg"]
        self.ffprobe = cfg["ffprobe"]
        self.audiocpp = cfg["audiocpp"]
        self.model = cfg["model"]
        self.source = cfg["input"]
        self.ref = cfg["ref"]
        self.output = cfg["output"]
        self.threads = detect_cpu_threads()
        self.log = log
        self.should_cancel = should_cancel
        self._proc = None
        self._proc_lock = threading.Lock()

    # ---------- 子进程 ----------

    def _check_cancel(self):
        if self.should_cancel():
            self.terminate()
            raise ConversionCancelled()

    def terminate(self):
        with self._proc_lock:
            if self._proc is not None and self._proc.poll() is None:
                try:
                    self._proc.kill()
                except OSError:
                    pass

    def run_logged(self, cmd, desc):
        """运行命令, 实时把输出写入日志; 返回 exit code。"""
        self.log("$ " + " ".join('"%s"' % c if " " in c else c for c in cmd))
        self._check_cancel()
        try:
            with self._proc_lock:
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                    errors="replace",
                    creationflags=CREATE_NO_WINDOW,
                )
                proc = self._proc
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    self.log(line)
                self._check_cancel()
            rc = proc.wait()
        except OSError as exc:
            self.log("ERROR: 无法执行 %s: %s" % (cmd[0], exc))
            return 127
        finally:
            with self._proc_lock:
                self._proc = None
        if rc != 0:
            self.log("ERROR: %s 失败 (exit code %d)" % (desc, rc))
        return rc

    # ---------- 流水线各阶段 ----------

    def probe_duration(self, path):
        try:
            proc = subprocess.run(
                [self.ffprobe, "-v", "error",
                 "-show_entries", "format=duration",
                 "-of", "csv=p=0", path],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, errors="replace",
                creationflags=CREATE_NO_WINDOW)
        except OSError as exc:
            self.log("ERROR: 无法执行 ffprobe: %s" % exc)
            return None
        if proc.returncode != 0:
            self.log("ERROR: ffprobe 失败: " + proc.stderr.strip())
            return None
        try:
            return float(proc.stdout.strip())
        except ValueError:
            return None

    def detect_cut_points(self, wav_path):
        rc_holder = {}

        def run():
            proc = subprocess.Popen(
                [self.ffmpeg, "-i", wav_path,
                 "-af", "silencedetect=noise=%s:d=%g"
                 % (SILENCE_NOISE, SILENCE_MIN_DURATION),
                 "-f", "null", "-"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                universal_newlines=True, errors="replace",
                creationflags=CREATE_NO_WINDOW)
            with self._proc_lock:
                self._proc = proc
            cuts = []
            start = None
            for line in proc.stdout:
                self._check_cancel()
                m = SIL_START_RE.search(line)
                if m:
                    start = float(m.group(1))
                    continue
                m = SIL_END_RE.search(line)
                if m and start is not None:
                    cuts.append((start + float(m.group(1))) / 2.0)
                    start = None
            rc_holder["rc"] = proc.wait()
            with self._proc_lock:
                self._proc = None
            return cuts

        try:
            cuts = run()
        except OSError as exc:
            raise RuntimeError("无法执行 ffmpeg: %s" % exc)
        if rc_holder.get("rc", 1) != 0:
            self.log("WARNING: silencedetect exit code %s, 使用已检测到的切分点继续"
                     % rc_holder.get("rc"))
        return sorted(cuts)

    @staticmethod
    def build_segments(cut_points, total_duration, min_duration):
        segments = []
        prev = 0.0
        for cut in cut_points:
            if (cut > prev
                    and cut - prev >= min_duration
                    and total_duration - cut >= min_duration):
                segments.append((prev, cut))
                prev = cut
        if total_duration > prev:
            segments.append((prev, total_duration))
        return segments

    def extract_segments(self, src_wav, segments, seg_dir):
        files = []
        for index, (start, end) in enumerate(segments):
            self._check_cancel()
            seg_file = os.path.join(seg_dir, "seg_%04d.wav" % index)
            self.log("  提取分段 %d/%d: %.3fs - %.3fs"
                     % (index + 1, len(segments), start, end))
            rc = self.run_logged(
                [self.ffmpeg, "-y",
                 "-ss", "%.3f" % start,
                 "-i", src_wav,
                 "-t", "%.3f" % (end - start),
                 "-ar", str(SAMPLE_RATE), "-ac", "1", "-sample_fmt", "s16",
                 seg_file],
                "ffmpeg 提取分段 %d" % index)
            if rc != 0:
                raise RuntimeError("ffmpeg 提取分段 %d 失败 (%.3f - %.3f)"
                                   % (index, start, end))
            files.append(seg_file)
        return files

    def convert_segments(self, seg_files, conv_dir):
        converted = []
        count = len(seg_files)
        for i, seg_file in enumerate(seg_files):
            self._check_cancel()
            base = os.path.splitext(os.path.basename(seg_file))[0]
            out_file = os.path.join(conv_dir, base + "_vc.wav")
            self.log("(%d/%d) 转换分段 %s (threads: %d) ..."
                     % (i + 1, count, base, self.threads))
            rc = self.run_logged(
                [self.audiocpp,
                 "--task", "vc",
                 "--model", self.model,
                 "--threads", str(self.threads),
                 "--log",
                 "--audio", seg_file,
                 "--voice-ref", self.ref,
                 "--out", out_file],
                "audiocpp_cli 转换分段 %s" % base)
            if rc != 0:
                raise RuntimeError("audiocpp_cli 转换分段 %s 失败" % base)
            converted.append(out_file)
        return converted

    def concat_segments(self, converted_files, list_file, output_file):
        list_dir = os.path.dirname(os.path.abspath(list_file))
        with open(list_file, "w", encoding="utf-8") as f:
            for path in converted_files:
                rel = os.path.relpath(os.path.abspath(path), list_dir)
                f.write("file '%s'\n"
                        % rel.replace(os.sep, "/").replace("'", "'\\''"))
        rc = self.run_logged(
            [self.ffmpeg, "-y", "-f", "concat", "-safe", "0",
             "-i", list_file, "-c", "copy", output_file],
            "ffmpeg 合并输出")
        if rc != 0:
            raise RuntimeError("ffmpeg concat 合并失败")

    # ---------- 主流程 ----------

    def run(self):
        work_dir = tempfile.mkdtemp(prefix="vcui_work_")
        seg_dir = os.path.join(work_dir, "segments")
        conv_dir = os.path.join(work_dir, "converted")
        os.makedirs(seg_dir)
        os.makedirs(conv_dir)
        tmp_raw_wav = os.path.join(work_dir, "_tmp_source_24k.wav")

        try:
            self.log("=" * 60)
            self.log("开始转换: %s" % self.source)
            self.log("参考音色: %s" % self.ref)
            self.log("输出文件: %s" % self.output)
            self.log("=" * 60)

            self.log("[1/4] 将输入音频转为标准 wav: %dHz, 单声道, 16bit PCM"
                     % SAMPLE_RATE)
            if self.run_logged(
                    [self.ffmpeg, "-y", "-i", self.source,
                     "-ar", str(SAMPLE_RATE), "-ac", "1",
                     "-sample_fmt", "s16", tmp_raw_wav],
                    "ffmpeg 音频标准化") != 0:
                raise RuntimeError("ffmpeg 音频标准化失败")

            self.log("[2/4] 检测停顿并切分音频")
            total_duration = self.probe_duration(tmp_raw_wav)
            if total_duration is None:
                raise RuntimeError("ffprobe 获取音频时长失败")
            self.log("音频总时长: %.3f 秒" % total_duration)

            cut_points = self.detect_cut_points(tmp_raw_wav)
            self.log("检测到 %d 个切分点" % len(cut_points))
            segments = self.build_segments(
                cut_points, total_duration, MIN_SEGMENT_DURATION)

            seg_files = self.extract_segments(tmp_raw_wav, segments, seg_dir)
            self.log("共切分为 %d 个分段" % len(seg_files))
            if not seg_files:
                raise RuntimeError("没有产生任何音频分段")

            self.log("[3/4] 使用 audiocpp_cli 逐段转换音色 (%d 线程)"
                     % self.threads)
            converted = self.convert_segments(seg_files, conv_dir)

            self.log("[4/4] 合并转换后的分段到 %s" % self.output)
            out_dir = os.path.dirname(os.path.abspath(self.output))
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
            self.concat_segments(
                converted, os.path.join(work_dir, "concat_list.txt"),
                self.output)

            self.log("SUCCESS: 音色转换完成。输出文件: %s (共处理 %d 个分段)"
                     % (self.output, len(seg_files)))
        finally:
            if os.environ.get("KEEP_WORK", "0") != "1":
                shutil.rmtree(work_dir, ignore_errors=True)
            else:
                self.log("KEEP_WORK=1, 保留工作目录: %s" % work_dir)


class App:
    FIELDS = [
        ("ffmpeg", "ffmpeg 路径"),
        ("ffprobe", "ffprobe 路径"),
        ("audiocpp", "audiocpp 可执行程序"),
        ("model", "gguf 模型文件"),
        ("input", "输入音频文件"),
        ("ref", "参考音频文件"),
        ("output", "输出音频文件"),
    ]

    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("860x640")
        root.minsize(700, 480)

        self.cfg = self.load_config()
        self.entries = {}
        self.log_queue = queue.Queue()
        self.worker = None
        self.cancel_flag = threading.Event()

        self._build_ui()
        self._load_values()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self._poll_log_queue)

    # ---------- 配置读写 ----------

    def load_config(self):
        cfg = default_config()
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                for key in cfg:
                    if isinstance(saved.get(key), str):
                        cfg[key] = saved[key]
        except (OSError, ValueError):
            pass
        return cfg

    def save_config(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            messagebox.showerror(APP_TITLE, "保存配置失败: %s" % exc)

    # ---------- UI ----------

    def _build_ui(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista" if os.name == "nt" else "clam")
        except tk.TclError:
            pass

        frame = ttk.Frame(self.root, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(1, weight=1)

        for row, (key, label) in enumerate(self.FIELDS):
            ttk.Label(frame, text=label + ":").grid(
                row=row, column=0, sticky=tk.W, pady=3, padx=(0, 8))
            var = tk.StringVar()
            entry = ttk.Entry(frame, textvariable=var)
            entry.grid(row=row, column=1, sticky=tk.EW, pady=3)
            self.entries[key] = var
            ttk.Button(frame, text="浏览...", width=8,
                       command=lambda k=key: self.browse(k)).grid(
                row=row, column=2, sticky=tk.E, pady=3, padx=(8, 0))

        btns = ttk.Frame(frame)
        btns.grid(row=len(self.FIELDS), column=0, columnspan=3,
                  sticky=tk.EW, pady=(10, 6))
        self.convert_btn = ttk.Button(btns, text="转换", command=self.on_convert)
        self.convert_btn.pack(side=tk.LEFT)
        self.cancel_btn = ttk.Button(
            btns, text="取消", command=self.on_cancel, state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.LEFT, padx=8)
        ttk.Button(btns, text="清除日志",
                   command=self.on_clear_log).pack(side=tk.LEFT)

        self.log_text = scrolledtext.ScrolledText(
            frame, state=tk.DISABLED, wrap=tk.NONE,
            font=("Consolas", 9), background="#1e1e1e", foreground="#d4d4d4")
        self.log_text.grid(row=len(self.FIELDS) + 1, column=0, columnspan=3,
                           sticky=tk.NSEW)
        frame.rowconfigure(len(self.FIELDS) + 1, weight=1)

        self.status = tk.StringVar(value="就绪")
        ttk.Label(frame, textvariable=self.status, anchor=tk.W).grid(
            row=len(self.FIELDS) + 2, column=0, columnspan=3,
            sticky=tk.EW, pady=(6, 0))

    def _load_values(self):
        for key, _ in self.FIELDS:
            self.entries[key].set(self.cfg.get(key, ""))

    def browse(self, key):
        current = self.entries[key].get()
        initial_dir = os.path.dirname(current) if current else SCRIPT_DIR
        if key in ("ffmpeg", "ffprobe", "audiocpp"):
            path = filedialog.askopenfilename(
                title="选择 %s" % key,
                initialdir=initial_dir,
                filetypes=[("可执行程序", EXE_PATTERN), ("所有文件", "*.*")])
        elif key == "model":
            path = filedialog.askopenfilename(
                title="选择 gguf 模型文件", initialdir=initial_dir,
                filetypes=[("gguf 模型", "*.gguf"), ("所有文件", "*.*")])
        elif key in ("input", "ref"):
            path = filedialog.askopenfilename(
                title="选择音频文件", initialdir=initial_dir,
                filetypes=AUDIO_FILETYPES)
        else:  # output
            path = filedialog.asksaveasfilename(
                title="选择输出音频文件", initialdir=initial_dir,
                defaultextension=".wav",
                initialfile=os.path.basename(current) or "vc_out.wav",
                filetypes=[("wav 音频", "*.wav"), ("所有文件", "*.*")])
        if path:
            self.entries[key].set(path)

    # ---------- 日志 ----------

    def log(self, message):
        self.log_queue.put(message)

    def _poll_log_queue(self):
        try:
            while True:
                message = self.log_queue.get_nowait()
                if isinstance(message, tuple):
                    kind, value = message
                    if kind == "__status__":
                        self.status.set(value)
                    elif kind == "__done__":
                        self.convert_btn.configure(state=tk.NORMAL)
                        self.cancel_btn.configure(state=tk.DISABLED)
                    continue
                self.log_text.configure(state=tk.NORMAL)
                self.log_text.insert(tk.END, message + "\n")
                self.log_text.see(tk.END)
                self.log_text.configure(state=tk.DISABLED)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_log_queue)

    def on_clear_log(self):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    # ---------- 转换 ----------

    def collect_config(self):
        cfg = {key: self.entries[key].get().strip()
               for key, _ in self.FIELDS}
        self.cfg = cfg
        return cfg

    def validate_config(self, cfg):
        for key in ("ffmpeg", "ffprobe", "audiocpp", "model", "input", "ref"):
            if not cfg[key]:
                label = dict(self.FIELDS)[key]
                messagebox.showwarning(APP_TITLE, "请先选择 %s" % label)
                return False
        for key in ("ffmpeg", "ffprobe", "audiocpp"):
            if not os.path.isfile(cfg[key]):
                messagebox.showerror(
                    APP_TITLE, "%s 不存在:\n%s" % (key, cfg[key]))
                return False
        for key in ("model", "input", "ref"):
            if not os.path.isfile(cfg[key]):
                messagebox.showerror(
                    APP_TITLE, "%s 文件不存在:\n%s" % (key, cfg[key]))
                return False
        if not cfg["output"]:
            messagebox.showwarning(APP_TITLE, "请先选择输出音频文件路径")
            return False
        return True

    def on_convert(self):
        cfg = self.collect_config()
        self.save_config()
        if not self.validate_config(cfg):
            return

        self.cancel_flag.clear()
        self.convert_btn.configure(state=tk.DISABLED)
        self.cancel_btn.configure(state=tk.NORMAL)
        self.status.set("转换中...")

        def work():
            converter = Converter(cfg, self.log, self.cancel_flag.is_set)
            try:
                converter.run()
                self.log_queue.put(("__status__", "转换完成"))
            except ConversionCancelled:
                self.log_queue.put("已取消转换")
                self.log_queue.put(("__status__", "已取消"))
            except Exception as exc:  # noqa: BLE001 - 展示给用户
                self.log_queue.put("ERROR: %s" % exc)
                self.log_queue.put(("__status__", "转换失败"))
            finally:
                self.log_queue.put(("__done__", None))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def on_cancel(self):
        self.cancel_flag.set()
        self.status.set("正在取消...")

    def on_close(self):
        if self.worker is not None and self.worker.is_alive():
            if not messagebox.askyesno(
                    APP_TITLE, "转换正在进行中, 确定要退出吗?"):
                return
            self.cancel_flag.set()
        self.save_config()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
