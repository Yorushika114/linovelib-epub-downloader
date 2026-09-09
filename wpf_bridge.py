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

EVENT_PREFIX = "@@LINOVELIB_EVENT@@"


def event_to_json(event: DownloadEvent) -> str:
    """Encode one download event as one UTF-8-safe JSON line."""
    payload = {
        _camel_case(key): value
        for key, value in dataclasses.asdict(event).items()
    }
    return EVENT_PREFIX + json.dumps(payload, ensure_ascii=False)


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


def comic_resolve_hits_json(text: str, fetcher=None) -> list[dict]:
    """把漫画书名解析为候选列表（不选取），返回可 JSON 化的 dict 列表。

    与下载解耦：WPF 在下载前调用它做书名筛选；每条含 kind=search_hit、id、title、exact。
    id 恒为 str（与 WPF ResolveResultDto.Id 一致）。
    """
    from linovelib.resolver import is_exact_match
    if fetcher is None:
        from comic.fetcher import ComicFetcher
        fetcher = ComicFetcher()
    hits = fetcher.search(text)
    return [
        {"kind": "search_hit", "id": str(h.id), "title": h.title,
         "exact": is_exact_match(text, h.title)}
        for h in hits
    ]


def run_comic_resolve(text: str) -> int:
    """仅按书名解析漫画候选（不下载），把每条候选以一行 JSON 打到 stdout 供 WPF 选择。"""
    fetcher = None
    try:
        from comic.fetcher import ComicFetcher
        fetcher = ComicFetcher()
        with contextlib.redirect_stdout(sys.stderr):
            items = comic_resolve_hits_json(text, fetcher=fetcher)
    except Exception as exc:
        # 与小说 run_resolve 一致：解析通路读的是无前缀 JSON，故错误也输出为裸 JSON 一行。
        print(json.dumps({"kind": "search_error", "message": str(exc)}, ensure_ascii=False),
              file=sys.__stdout__, flush=True)
        return 1
    finally:
        if fetcher is not None:
            try:
                fetcher.close()
            except Exception:
                pass
    for item in items:
        print(json.dumps(item, ensure_ascii=False), file=sys.__stdout__, flush=True)
    print(json.dumps({"kind": "search_done", "total": len(items)},
                     ensure_ascii=False), file=sys.__stdout__, flush=True)
    return 0


def run_comic(argv: list[str] | None = None) -> int:
    """驱动漫画下载（comic.cli.main），输出桥事件并可响应安全取消。"""
    cancel_event = threading.Event()
    threading.Thread(
        target=read_cancel_commands, args=(sys.stdin, cancel_event), daemon=True
    ).start()
    try:
        with contextlib.redirect_stdout(sys.stderr):
            from comic.cli import main as comic_main
            return comic_main(argv, observer=emit_json_event, cancel_event=cancel_event)
    except Exception as exc:
        emit_json_event(DownloadEvent("worker_failed", message=str(exc)))
        return 1


if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv and argv[0] == "--resolve":
        raise SystemExit(run_resolve(argv[1]))
    if argv and argv[0] == "--resolve-comic":
        raise SystemExit(run_comic_resolve(argv[1]))
    if argv and argv[0] == "--comic":
        raise SystemExit(run_comic(argv))
    raise SystemExit(run())
