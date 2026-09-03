"""JSON-lines adapter between the downloader core and the WPF desktop app."""

from __future__ import annotations

import contextlib
import dataclasses
import json
import sys
import threading
from typing import TextIO

import main as downloader_main
from linovelib.events import DownloadEvent


def event_to_json(event: DownloadEvent) -> str:
    """Encode one download event as one UTF-8-safe JSON line."""
    return json.dumps(dataclasses.asdict(event), ensure_ascii=False)


def emit_json_event(event: DownloadEvent) -> None:
    print(event_to_json(event), file=sys.__stdout__, flush=True)


def read_cancel_commands(stream: TextIO, cancel_event: threading.Event) -> None:
    """Set the shared event when the WPF process requests a safe cancel."""
    for line in stream:
        if line.strip().lower() == "cancel":
            cancel_event.set()
            return


def run(argv: list[str] | None = None) -> int:
    cancel_event = threading.Event()
    threading.Thread(
        target=read_cancel_commands, args=(sys.stdin, cancel_event), daemon=True
    ).start()
    try:
        with contextlib.redirect_stdout(sys.stderr):
            return downloader_main.main(argv, observer=emit_json_event,
                                        cancel_event=cancel_event)
    except Exception as exc:
        emit_json_event(DownloadEvent("worker_failed", message=str(exc)))
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
