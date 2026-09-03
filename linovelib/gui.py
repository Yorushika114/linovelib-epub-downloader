"""Tkinter desktop interface for the downloader."""

from __future__ import annotations

from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageOps, ImageTk

from .events import DownloadEvent
from .gui_state import GuiDownloadState
from .paths import PROJECT_ROOT


def build_download_argv(novel_id: str, volumes: str, delay: str,
                        output_path: str) -> list[str]:
    """Turn form values into the existing command-line arguments."""
    argv = ["--novel", novel_id.strip()]
    selected = volumes.strip()
    if selected.lower() == "all":
        argv.extend(["--vol", "all"])
    elif selected:
        argv.extend(["--volumes", selected])
    if delay.strip():
        argv.extend(["--delay", delay.strip()])
    if output_path.strip():
        argv.extend(["--out", output_path.strip()])
    return argv


def run_download(download_main, argv: list[str], event_queue, cancel_event) -> int:
    """Run a download and forward every structured event into a queue."""
    try:
        result = download_main(argv, observer=event_queue.put,
                               cancel_event=cancel_event)
    except Exception as exc:
        result = 1
        event_queue.put(DownloadEvent("worker_failed", message=str(exc)))
    event_queue.put(DownloadEvent("worker_finished", message=str(result)))
    return result


class DownloadApp:
    """Responsive Tkinter front end for the EPUB downloader."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("轻小说 EPUB 下载器")
        self.root.geometry("1160x760")
        self.root.minsize(940, 620)

        self.events: queue.Queue[DownloadEvent] = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.state = GuiDownloadState()
        self.row_items: dict[str, str] = {}
        self._background_photo = None
        self._background_image = Image.open(
            PROJECT_ROOT / "assets" / "lightnovel-reader-background.png").convert("RGB")

        self.novel_id = tk.StringVar()
        self.volumes = tk.StringVar(value="all")
        self.delay = tk.StringVar(value="0.4")
        self.output_path = tk.StringVar()
        self.status = tk.StringVar(value="填写小说编号与卷号后开始下载。")
        self.output = tk.StringVar(value="尚未生成 EPUB。")

        self._build_window()
        self.root.after(80, self._drain_events)

    def _build_window(self) -> None:
        self.canvas = tk.Canvas(self.root, highlightthickness=0, background="#0b1024")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._resize_background)

        self.panel = tk.Frame(self.canvas, bg="#11182b", padx=22, pady=18)
        self.panel_window = self.canvas.create_window(22, 20, anchor="nw", window=self.panel)
        self._build_panel()

    def _build_panel(self) -> None:
        self.panel.grid_columnconfigure(0, weight=1)
        self.panel.grid_rowconfigure(3, weight=1)

        title = tk.Label(self.panel, text="轻小说 EPUB 下载器", bg="#11182b", fg="#f4f1ff",
                         font=("Microsoft YaHei UI", 22, "bold"))
        title.grid(row=0, column=0, sticky="w")
        subtitle = tk.Label(
            self.panel, text="按章节跟踪下载进度，已完成的卷会即时生成 EPUB。",
            bg="#11182b", fg="#aeb8d9", font=("Microsoft YaHei UI", 10))
        subtitle.grid(row=1, column=0, sticky="w", pady=(2, 14))

        form = tk.Frame(self.panel, bg="#11182b")
        form.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        for column in (1, 3):
            form.grid_columnconfigure(column, weight=1)
        self._field(form, "小说编号", self.novel_id, 0, 0, "例如 3095")
        self._field(form, "卷号", self.volumes, 0, 2, "all 或 1-3,5")
        self._field(form, "请求间隔（秒）", self.delay, 1, 0, "默认 0.4")
        self._field(form, "输出 EPUB（可选）", self.output_path, 1, 2, "留空则按卷输出")
        browse = tk.Button(form, text="选择…", command=self._choose_output,
                           bg="#2b3d75", fg="#ffffff", activebackground="#3b529b",
                           activeforeground="#ffffff", relief="flat", padx=10)
        browse.grid(row=1, column=4, padx=(6, 0), pady=5)

        controls = tk.Frame(self.panel, bg="#11182b")
        controls.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        self.start_button = tk.Button(
            controls, text="开始下载", command=self.start_download, bg="#7c5cff",
            fg="#ffffff", activebackground="#9279ff", activeforeground="#ffffff",
            relief="flat", padx=18, pady=8, font=("Microsoft YaHei UI", 10, "bold"))
        self.start_button.pack(side="left")
        self.cancel_button = tk.Button(
            controls, text="取消下载", command=self.cancel_download, state="disabled",
            bg="#38415e", fg="#ffffff", activebackground="#566080",
            activeforeground="#ffffff", relief="flat", padx=18, pady=8)
        self.cancel_button.pack(side="left", padx=(8, 0))
        tk.Label(controls, textvariable=self.status, bg="#11182b", fg="#d6dcf5",
                 anchor="w", font=("Microsoft YaHei UI", 10)).pack(
                     side="left", fill="x", expand=True, padx=(16, 0))

        progress_frame = tk.Frame(self.panel, bg="#11182b")
        progress_frame.grid(row=4, column=0, sticky="ew", pady=(0, 12))
        progress_frame.grid_columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(progress_frame, orient="horizontal",
                                        mode="determinate", maximum=1, value=0)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.progress_label = tk.Label(progress_frame, text="0 / 0 章", bg="#11182b",
                                       fg="#aeb8d9", anchor="e")
        self.progress_label.grid(row=1, column=0, sticky="e", pady=(3, 0))

        content = tk.PanedWindow(self.panel, orient="horizontal", sashwidth=5,
                                 bg="#11182b", bd=0, relief="flat")
        content.grid(row=5, column=0, sticky="nsew")
        self.panel.grid_rowconfigure(5, weight=1)

        chapters = tk.Frame(content, bg="#19223d", padx=12, pady=10)
        content.add(chapters, stretch="always")
        tk.Label(chapters, text="章节状态", bg="#19223d", fg="#f4f1ff",
                 font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", pady=(0, 7))
        columns = ("volume", "chapter", "status", "detail")
        self.chapter_table = ttk.Treeview(chapters, columns=columns, show="headings",
                                          height=16)
        for name, text, width in (
                ("volume", "卷", 105), ("chapter", "章节", 180),
                ("status", "状态", 72), ("detail", "说明", 130)):
            self.chapter_table.heading(name, text=text)
            self.chapter_table.column(name, width=width, anchor="w")
        table_scroll = ttk.Scrollbar(chapters, orient="vertical",
                                    command=self.chapter_table.yview)
        self.chapter_table.configure(yscrollcommand=table_scroll.set)
        self.chapter_table.pack(side="left", fill="both", expand=True)
        table_scroll.pack(side="right", fill="y")

        log_box = tk.Frame(content, bg="#19223d", padx=12, pady=10)
        content.add(log_box, minsize=260)
        tk.Label(log_box, text="下载日志", bg="#19223d", fg="#f4f1ff",
                 font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", pady=(0, 7))
        self.log = tk.Text(log_box, wrap="word", height=16, state="disabled",
                           bg="#0f1629", fg="#d6dcf5", insertbackground="#ffffff",
                           relief="flat", padx=8, pady=8, font=("Consolas", 9))
        self.log.pack(fill="both", expand=True)

        footer = tk.Label(self.panel, textvariable=self.output, bg="#11182b",
                          fg="#b7c7ff", anchor="w", wraplength=1000)
        footer.grid(row=6, column=0, sticky="ew", pady=(12, 0))

    def _field(self, parent, label: str, variable: tk.StringVar,
               row: int, column: int, hint: str) -> None:
        tk.Label(parent, text=f"{label}（{hint}）", bg="#11182b", fg="#c7d0ee").grid(
            row=row, column=column, sticky="w", padx=(0, 7), pady=5)
        entry = tk.Entry(parent, textvariable=variable, bg="#0d1426", fg="#f5f7ff",
                         insertbackground="#ffffff", relief="flat", width=28)
        entry.grid(row=row, column=column + 1, sticky="ew", pady=5)
        entry.configure(takefocus=True)

    def _choose_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title="保存 EPUB", defaultextension=".epub",
            filetypes=[("EPUB 电子书", "*.epub")])
        if path:
            self.output_path.set(path)

    def start_download(self) -> None:
        novel_id = self.novel_id.get().strip()
        volumes = self.volumes.get().strip()
        if not novel_id:
            messagebox.showwarning("缺少小说编号", "请输入小说页面 URL 中的小说编号。")
            return
        if not volumes:
            messagebox.showwarning("缺少卷号", "请输入 all 或卷号，例如 1-3,5。")
            return
        try:
            argv = build_download_argv(novel_id, volumes, self.delay.get(),
                                       self.output_path.get())
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc))
            return

        self.events = queue.Queue()
        self.cancel_event = threading.Event()
        self.state = GuiDownloadState()
        self.row_items.clear()
        for item in self.chapter_table.get_children():
            self.chapter_table.delete(item)
        self._write_log("开始下载任务。")
        self.status.set("正在连接并读取目录…")
        self.output.set("尚未生成 EPUB。")
        self.progress.configure(maximum=1, value=0)
        self.progress_label.configure(text="0 / 0 章")
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")

        from main import main as download_main
        self.worker = threading.Thread(target=run_download,
                                       args=(download_main, argv, self.events,
                                             self.cancel_event), daemon=True)
        self.worker.start()

    def cancel_download(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            self.cancel_event.set()
            self.cancel_button.configure(state="disabled")
            self.status.set("已请求取消：当前章节结束后停止。")
            self._write_log("已请求安全取消，等待当前章节结束。")

    def _drain_events(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event.kind == "worker_finished":
                self._finish_worker(event.message)
                continue
            self.state.apply(event)
            self._render_state()
        self.root.after(80, self._drain_events)

    def _render_state(self) -> None:
        self.status.set(self.state.status_text)
        maximum = max(self.state.total, 1)
        self.progress.configure(maximum=maximum, value=self.state.completed)
        self.progress_label.configure(text=f"{self.state.completed} / {self.state.total} 章")
        for chapter_id, row in self.state.rows.items():
            values = (row.volume_title, row.title, row.status, row.message)
            item = self.row_items.get(chapter_id)
            if item is None:
                self.row_items[chapter_id] = self.chapter_table.insert(
                    "", "end", values=values)
            else:
                self.chapter_table.item(item, values=values)
        if self.state.logs:
            self._write_log(self.state.logs[-1])
        if self.state.output_paths:
            self.output.set("已生成：" + "； ".join(self.state.output_paths))

    def _finish_worker(self, result: str) -> None:
        self.start_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        if not self.state.finished:
            self.status.set("下载任务结束。" if result == "0" else f"下载结束，返回码 {result}。")
        self._write_log(f"下载任务已结束（返回码 {result}）。")

    def _write_log(self, text: str) -> None:
        if not text:
            return
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _resize_background(self, event) -> None:
        if event.width < 2 or event.height < 2:
            return
        image = ImageOps.fit(self._background_image, (event.width, event.height),
                             Image.Resampling.LANCZOS)
        self._background_photo = ImageTk.PhotoImage(image)
        self.canvas.delete("background")
        self.canvas.create_image(0, 0, image=self._background_photo, anchor="nw",
                                 tags="background")
        self.canvas.tag_lower("background")
        self.canvas.coords(self.panel_window, 22, 20)
        self.canvas.itemconfigure(self.panel_window,
                                  width=max(event.width - 44, 1),
                                  height=max(event.height - 40, 1))


def launch() -> None:
    root = tk.Tk()
    DownloadApp(root)
    root.mainloop()
