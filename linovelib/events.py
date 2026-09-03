"""Structured, optional progress events for download front ends."""

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class DownloadEvent:
    """One observable state change produced by a download run."""

    kind: str
    volume_index: int | None = None
    volume_title: str = ""
    chapter_id: str = ""
    chapter_title: str = ""
    completed: int = 0
    total: int = 0
    message: str = ""
    output_path: str = ""


def emit(observer: Callable[[DownloadEvent], None] | None,
         event: DownloadEvent) -> None:
    """Deliver an event when a caller opted into observing progress."""
    if observer is not None:
        observer(event)
