"""Desktop UI: video + SRT subtitle -> still images with GPS EXIF."""

from __future__ import annotations

import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from extract_frames import find_ffmpeg, find_pairs, run_batch, stop_current_ffmpeg
from repair_for_itwin import repair_root

TOOL_DIR = Path(__file__).resolve().parent
ROOT = TOOL_DIR.parent


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("视频字幕抽帧")
        self.geometry("820x640")
        self.minsize(720, 540)

        self.input_var = tk.StringVar(value=str(ROOT))
        self.output_var = tk.StringVar(value=str(ROOT / "output"))
        self.interval_var = tk.StringVar(value="1")
        self.trial_var = tk.StringVar(value="")
        self.overlay_var = tk.BooleanVar(value=False)
        self.gpx_var = tk.BooleanVar(value=True)
        self.pair_var = tk.StringVar(value="尚未扫描")
        self.busy = False
        self.cancel = threading.Event()

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(200, self._check_ffmpeg)
        self.after(300, self._scan_pairs)
        self.input_var.trace_add("write", lambda *_: self.after(400, self._scan_pairs))

    def _build(self) -> None:
        pad = {"padx": 12, "pady": 6}
        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(
            frm,
            text="把 MP4/MOV 和同名 SRT 按间隔导出成 JPEG，并把字幕里的 GPS 写入图片。",
            wraplength=760,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        ttk.Label(frm, text="视频目录").grid(row=1, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.input_var).grid(row=1, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="浏览", command=self._pick_input).grid(row=1, column=2)

        ttk.Label(frm, text="配对结果").grid(row=2, column=0, sticky="w")
        ttk.Label(frm, textvariable=self.pair_var).grid(row=2, column=1, columnspan=2, sticky="w", **pad)

        ttk.Label(frm, text="输出目录").grid(row=3, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.output_var).grid(row=3, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="浏览", command=self._pick_output).grid(row=3, column=2)

        ttk.Label(frm, text="抽帧间隔（秒）").grid(row=4, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.interval_var, width=10).grid(row=4, column=1, sticky="w", **pad)

        ttk.Label(frm, text="试跑秒数（留空=全部）").grid(row=5, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.trial_var, width=10).grid(row=5, column=1, sticky="w", **pad)

        opts = ttk.Frame(frm)
        opts.grid(row=6, column=0, columnspan=3, sticky="w", pady=(4, 8))
        ttk.Checkbutton(
            opts,
            text="画面叠加 GPS（仅查看；导入 iTwin / Postshot 请勿勾选）",
            variable=self.overlay_var,
        ).pack(side="left", padx=(0, 16))
        ttk.Checkbutton(opts, text="导出 GPX 航迹", variable=self.gpx_var).pack(side="left")

        btns = ttk.Frame(frm)
        btns.grid(row=7, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self.start_btn = ttk.Button(btns, text="开始抽帧", command=self._start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(btns, text="停止", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=8)
        ttk.Button(btns, text="扫描配对", command=self._scan_pairs).pack(side="left")
        self.repair_btn = ttk.Button(btns, text="修复已有 JPEG", command=self._repair)
        self.repair_btn.pack(side="left", padx=8)
        ttk.Button(btns, text="打开输出目录", command=self._open_output).pack(side="left")

        ttk.Label(frm, text="日志").grid(row=8, column=0, sticky="nw")
        self.log_box = tk.Text(frm, height=18, wrap="word")
        self.log_box.grid(row=9, column=0, columnspan=3, sticky="nsew")
        frm.rowconfigure(9, weight=1)
        scroll = ttk.Scrollbar(frm, command=self.log_box.yview)
        scroll.grid(row=9, column=3, sticky="ns")
        self.log_box.configure(yscrollcommand=scroll.set)

    def _pick_input(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.input_var.get() or str(ROOT))
        if chosen:
            self.input_var.set(chosen)
            current_out = Path(self.output_var.get()) if self.output_var.get() else None
            if current_out is None or current_out.name in {"output", ""}:
                self.output_var.set(str(Path(chosen) / "output"))

    def _pick_output(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.output_var.get() or str(ROOT))
        if chosen:
            self.output_var.set(chosen)

    def _append_log(self, message: str) -> None:
        try:
            self.log_box.insert("end", message + "\n")
            self.log_box.see("end")
            self.update_idletasks()
        except tk.TclError:
            pass

    def _check_ffmpeg(self) -> None:
        try:
            exe = find_ffmpeg()
            self._append_log(f"已找到 FFmpeg: {exe}")
        except Exception as exc:
            self._append_log(f"未找到 FFmpeg: {exc}")

    def _scan_pairs(self) -> None:
        input_dir = Path(self.input_var.get().strip() or ROOT)
        if not input_dir.exists():
            self.pair_var.set("目录不存在")
            return
        try:
            pairs = find_pairs(input_dir)
        except Exception as exc:
            self.pair_var.set(f"扫描失败: {exc}")
            return
        if not pairs:
            self.pair_var.set("没有找到 MP4/MOV + 同名 SRT")
            return
        names = "、".join(video.stem for video, _ in pairs[:8])
        extra = f" 等 {len(pairs)} 组" if len(pairs) > 8 else f"，共 {len(pairs)} 组"
        self.pair_var.set(names + extra)

    def _parse_common(self) -> tuple[Path, Path, float, float | None] | None:
        try:
            interval = float(self.interval_var.get().strip())
            if interval <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("参数错误", "抽帧间隔必须是大于 0 的数字")
            return None

        trial_text = self.trial_var.get().strip()
        max_duration = None
        if trial_text:
            try:
                max_duration = float(trial_text)
                if max_duration <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("参数错误", "试跑秒数必须是大于 0 的数字，或留空")
                return None

        input_dir = Path(self.input_var.get().strip())
        output_dir = Path(self.output_var.get().strip() or (input_dir / "output"))
        if not input_dir.exists():
            messagebox.showerror("目录不存在", str(input_dir))
            return None
        return input_dir, output_dir, interval, max_duration

    def _start(self) -> None:
        if self.busy:
            return
        parsed = self._parse_common()
        if parsed is None:
            return
        input_dir, output_dir, interval, max_duration = parsed

        self._set_busy(True)
        self._append_log("开始抽帧...")

        def work() -> None:
            try:
                summaries = run_batch(
                    input_dir,
                    output_dir,
                    interval=interval,
                    max_duration=max_duration,
                    overlay=self.overlay_var.get(),
                    export_gpx=self.gpx_var.get(),
                    log=lambda msg: self.after(0, self._append_log, msg),
                    cancel=self.cancel,
                )
                total = sum(item["extracted"] for item in summaries)
                gps_ok = sum(item["gps_written"] for item in summaries)
                halted = self.cancel.is_set()
                title = "已停止" if halted else "完成"
                text = (
                    f"{'已停止。' if halted else ''}"
                    f"共处理 {len(summaries)} 段视频，输出 {total} 张图片\n"
                    f"已写入 GPS：{gps_ok}/{total}\n{output_dir}"
                )
                self.after(0, lambda: self._notify("info", title, text))
            except Exception as exc:
                err = str(exc)
                self.after(0, lambda e=err: self._notify("error", "处理失败", e))
                self.after(0, self._append_log, f"失败: {err}")
            finally:
                self.after(0, self._done)

        threading.Thread(target=work, daemon=True).start()

    def _repair(self) -> None:
        if self.busy:
            return
        src = filedialog.askdirectory(
            title="选择已有 JPEG 目录",
            initialdir=self.output_var.get() or str(ROOT),
        )
        if not src:
            return
        dst = filedialog.askdirectory(
            title="选择修复后的输出目录",
            initialdir=str(Path(src).parent / "output_itwin"),
        )
        if not dst:
            return

        self._set_busy(True)
        self._append_log(f"修复 JPEG: {src} -> {dst}")

        def work() -> None:
            try:
                total = repair_root(
                    Path(src),
                    Path(dst),
                    log=lambda msg: self.after(0, self._append_log, msg),
                    cancel=self.cancel,
                )
                halted = self.cancel.is_set()
                title = "已停止" if halted else "完成"
                text = f"{'已停止。' if halted else ''}已修复 {total} 张\n{dst}"
                self.after(0, lambda: self._notify("info", title, text))
            except Exception as exc:
                err = str(exc)
                self.after(0, lambda e=err: self._notify("error", "修复失败", e))
                self.after(0, self._append_log, f"失败: {err}")
            finally:
                self.after(0, self._done)

        threading.Thread(target=work, daemon=True).start()

    def _open_output(self) -> None:
        path = Path(self.output_var.get().strip() or ROOT)
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    def _notify(self, kind: str, title: str, text: str) -> None:
        try:
            if kind == "info":
                messagebox.showinfo(title, text)
            else:
                messagebox.showerror(title, text)
        except tk.TclError:
            pass

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        try:
            if busy:
                self.cancel.clear()
                self.start_btn.configure(state="disabled")
                self.repair_btn.configure(state="disabled")
                self.stop_btn.configure(state="normal")
            else:
                self.start_btn.configure(state="normal")
                self.repair_btn.configure(state="normal")
                self.stop_btn.configure(state="disabled")
        except tk.TclError:
            pass

    def _stop(self) -> None:
        if not self.busy:
            return
        self.cancel.set()
        self.stop_btn.configure(state="disabled")
        stop_current_ffmpeg()
        self._append_log("正在停止，已导出的图片会保留...")

    def _on_close(self) -> None:
        if self.busy:
            self._stop()
        self.destroy()

    def _done(self) -> None:
        self._set_busy(False)
        self._append_log("可以继续处理，或关闭窗口。")


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
