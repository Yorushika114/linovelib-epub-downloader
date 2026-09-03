"""Presentation-independent state for the desktop downloader window."""

from dataclasses import dataclass

from .events import DownloadEvent


@dataclass
class ChapterRow:
    chapter_id: str
    title: str
    volume_title: str = ""
    status: str = "等待中"
    message: str = ""


class GuiDownloadState:
    """Translate download events into data a Tkinter view can render."""

    def __init__(self):
        self.rows: dict[str, ChapterRow] = {}
        self.completed = 0
        self.total = 0
        self.status_text = "等待开始下载。"
        self.logs: list[str] = []
        self.output_paths: list[str] = []
        self.finished = False

    def apply(self, event: DownloadEvent) -> None:
        if event.total:
            self.total = event.total
        if event.kind == "download_started":
            self.status_text = event.message or "正在准备下载。"
        elif event.kind == "chapter_pending":
            self.rows[event.chapter_id] = ChapterRow(
                chapter_id=event.chapter_id, title=event.chapter_title,
                volume_title=event.volume_title)
        elif event.kind == "chapter_started":
            row = self._row(event)
            row.status = "下载中"
            self.status_text = f"正在下载：{row.title}"
        elif event.kind == "chapter_finished":
            row = self._row(event)
            row.status = "已完成"
            self.completed = event.completed
            self.status_text = f"已完成：{row.title}"
        elif event.kind == "chapter_failed":
            row = self._row(event)
            row.status = "失败"
            row.message = event.message
            self.status_text = f"{row.title}下载失败：{event.message}"
        elif event.kind == "epub_written":
            self.output_paths.append(event.output_path)
            self.status_text = event.message or f"已生成：{event.output_path}"
        elif event.kind == "cancelled":
            self.completed = event.completed
            self.status_text = event.message or "下载已取消。"
            self.finished = True
        elif event.kind == "worker_failed":
            self.status_text = f"下载任务异常：{event.message}"
            self.finished = True
        elif event.kind == "finished":
            self.completed = event.completed
            self.status_text = event.message or "下载结束。"
            self.finished = True
        if event.message:
            self.logs.append(event.message)

    def _row(self, event: DownloadEvent) -> ChapterRow:
        row = self.rows.get(event.chapter_id)
        if row is None:
            row = ChapterRow(event.chapter_id, event.chapter_title,
                             event.volume_title)
            self.rows[event.chapter_id] = row
        if event.chapter_title:
            row.title = event.chapter_title
        if event.volume_title:
            row.volume_title = event.volume_title
        return row
