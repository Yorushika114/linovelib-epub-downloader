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


def novel_catalog_json(nid: str, fetcher=None) -> list[dict]:
    """只取小说目录页，返回「卷」级别的一行行 JSON（不展开章节、不下载）。

    为什么必须单独一条通路：`resolve` 只回 id/title/exact，卷列表得另取；没有它，
    WPF 就只能先起一次下载才知道这部作品有几卷——而用户要在下载**前**选卷。

    成本：目录页是普通 HTML，`Fetcher.get_html` 走 requests，**不需要浏览器**。
    逐卷页（vol_*.html）是「权威章节列表」，但本功能只要卷级信息，故不逐卷抓。
    """
    from linovelib.catalog import BASE, parse_catalog
    from linovelib.fetcher import Fetcher
    if fetcher is None:
        fetcher = Fetcher()
    html = fetcher.get_html(f"{BASE}/novel/{nid}/catalog")
    rows = [
        {"kind": "volume", "index": i, "title": vol.title,
         "chapters": len(vol.chapters), "vid": getattr(vol, "vid", "")}
        for i, vol in enumerate(parse_catalog(html, nid), start=1)
    ]
    rows.append({"kind": "catalog_done", "total": len(rows)})
    return rows


def comic_catalog_json(cid: str, fetcher=None) -> list[dict]:
    """只取漫画目录，返回「卷」级别的一行行 JSON。

    比小说侧贵得多：`get_catalog` 要 Playwright + 已暖机的 msedge，且目录页懒渲染
    （逐屏滚动才吐出全部卷）。故只走一次，结果直接复用，不要按卷重复调用。
    """
    if fetcher is None:
        from comic.fetcher import ComicFetcher
        fetcher = ComicFetcher()
    comic = fetcher.get_catalog(int(cid))
    rows = [
        {"kind": "volume", "index": getattr(vol, "index", i), "title": vol.title,
         "chapters": len(vol.chapters), "vid": ""}
        for i, vol in enumerate(comic.volumes, start=1)
    ]
    rows.append({"kind": "catalog_done", "total": len(rows)})
    return rows


def _emit_catalog(rows: list[dict], error_kind: str) -> int:
    """把卷行逐条以**裸 JSON**打到 stdout（与 run_resolve 同一套读取协议）。

    取目录失败必须报错而不是静默给空列表：调用方会把空列表渲染成「本作 0 卷」，
    比「未找到」更容易让人以为书坏了。
    """
    try:
        for row in rows:
            print(json.dumps(row, ensure_ascii=False), file=sys.__stdout__, flush=True)
    except Exception as exc:
        print(json.dumps({"kind": error_kind, "message": str(exc)}, ensure_ascii=False),
              file=sys.__stdout__, flush=True)
        return 1
    return 0


def run_catalog(nid: str) -> int:
    """仅取小说卷列表（不下载），逐行裸 JSON 输出供 WPF 渲染卷选择表。"""
    fetcher = None
    try:
        from linovelib.fetcher import Fetcher
        fetcher = Fetcher()
        with contextlib.redirect_stdout(sys.stderr):
            rows = novel_catalog_json(nid, fetcher=fetcher)
    except Exception as exc:
        print(json.dumps({"kind": "catalog_error", "message": str(exc)}, ensure_ascii=False),
              file=sys.__stdout__, flush=True)
        return 1
    finally:
        if fetcher is not None:
            try:
                fetcher.close()
            except Exception:
                pass
    return _emit_catalog(rows, "catalog_error")


def run_comic_catalog(cid: str) -> int:
    """仅取漫画卷列表（不下载），逐行裸 JSON 输出。"""
    fetcher = None
    try:
        from comic.fetcher import ComicFetcher
        fetcher = ComicFetcher()
        with contextlib.redirect_stdout(sys.stderr):
            rows = comic_catalog_json(cid, fetcher=fetcher)
    except Exception as exc:
        print(json.dumps({"kind": "catalog_error", "message": str(exc)}, ensure_ascii=False),
              file=sys.__stdout__, flush=True)
        return 1
    finally:
        if fetcher is not None:
            try:
                fetcher.close()
            except Exception:
                pass
    return _emit_catalog(rows, "catalog_error")


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
    if argv and argv[0] == "--catalog":
        raise SystemExit(run_catalog(argv[1]))
    if argv and argv[0] == "--comic-catalog":
        raise SystemExit(run_comic_catalog(argv[1]))
    if argv and argv[0] == "--comic":
        raise SystemExit(run_comic(argv))
    raise SystemExit(run())
