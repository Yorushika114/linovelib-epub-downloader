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
    payload = {
        _camel_case(key): value
        for key, value in dataclasses.asdict(event).items()
    }
    return json.dumps(payload, ensure_ascii=False)


def _camel_case(key: str) -> str:
    head, *tail = key.split("_")
    return head + "".join(part.capitalize() for part in tail)


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


def resolve_hits_json(text: str, fetcher=None, browser=None) -> list[dict]:
    """把书名解析为候选列表（不选取），返回可 JSON 化的 dict 列表。

    与下载解耦：WPF 在下载前调用它，把「书名筛选/候选选择」作为独立一步，
    选定编号后才进入卷数/下载。每条含 kind=search_hit、id、title、exact（书名吻合）。
    """
    from linovelib.resolver import search_hits, is_exact_match
    from linovelib.fetcher import Fetcher
    if fetcher is None:
        fetcher = Fetcher()
    hits = search_hits(text, fetcher, browser=browser)
    return [
        {"kind": "search_hit", "id": h.id, "title": h.title,
         "exact": is_exact_match(text, h.title)}
        for h in hits
    ]


def run_resolve(text: str) -> int:
    """仅解析书名→候选列表（不下载），把每条候选以一行 JSON 打到 stdout 供 WPF 选择。"""
    browser = None
    try:
        from linovelib.render import RenderFetcher
        browser = RenderFetcher(headless=True)
    except Exception:
        browser = None
    try:
        items = resolve_hits_json(text, browser=browser)
    finally:
        if browser is not None:
            browser.close()
    for item in items:
        print(json.dumps(item, ensure_ascii=False), file=sys.__stdout__, flush=True)
    print(json.dumps({"kind": "search_done", "total": len(items)},
                     ensure_ascii=False), file=sys.__stdout__, flush=True)
    return 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv and argv[0] == "--resolve":
        raise SystemExit(run_resolve(argv[1]))
    raise SystemExit(run())
