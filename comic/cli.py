"""bilimanga 漫画下载终端入口（本地演示版；WPF 版经 wpf_bridge 复用本模块）。"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from linovelib.events import DownloadEvent, emit
from linovelib.paths import DEFAULT_DOWNLOAD_DIR, PROJECT_ROOT

from .fetcher import ComicError, ComicFetcher
from .models import Comic, Volume
from .pdf import assemble_pdf
from .progress import Progress

MANGA_DIR = DEFAULT_DOWNLOAD_DIR / "漫画"

_ILLEGAL = re.compile(r'[\\/:*?"<>|]')


def _safe_name(name: str) -> str:
    return _ILLEGAL.sub("_", name).strip() or "未命名"


def build_parsed_args(argv=None):
    p = argparse.ArgumentParser(prog="comic", description="bilimanga.net 漫画下载并合成 PDF")
    p.add_argument("--comic", help="漫画编号，如 1270")
    p.add_argument("--name", help="漫画书名（站点搜索；命中多条时优先精确同名）")
    p.add_argument("--vol", "--volume", dest="vol", default=None,
                   help="选择的卷（从 1 起；支持逗号与 '-' 区间，如 1 或 1-3,5）")
    p.add_argument("--out", default=None, help="输出目录。默认 download/漫画/")
    p.add_argument("--delay", type=float, default=0.5, help="章节间请求间隔秒（默认 0.5）")
    p.add_argument("--headless", action="store_true", default=True,
                   help="无头模式（默认）")
    p.add_argument("--headful", action="store_true", help="显示浏览器窗口（调试用）")
    return p.parse_args(argv)


def _parse_vol_spec(spec: str | None, n: int) -> list[int]:
    if not spec or spec == "all":
        return list(range(1, n + 1))
    idxs: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            idxs.update(range(int(a), int(b) + 1))
        else:
            idxs.add(int(part))
    out = sorted(idxs)
    for i in out:
        if i < 1 or i > n:
            raise ValueError(f"卷号 {i} 超出范围（本作共 {n} 卷）")
    return out


def _pick_hit(hits, name: str):
    """命中多条时优先精确同名，否则取第一条。"""
    if not hits:
        raise ComicError(
            f"未搜到书名「{name}」相关结果。站内搜索按分词匹配，完整书名常搜不到；"
            f"请改用更简短的书名词（如去掉英文副标题），或直接用 --comic 编号。")
    for h in hits:
        if h.title == name:
            return h
    return hits[0]


def main(argv=None, *, observer=None, cancel_event=None) -> int:
    args = build_parsed_args(argv)
    if not args.comic and not args.name:
        print("请提供 --comic <编号> 或 --name <书名>")
        return 1

    out_root = Path(args.out) if args.out else MANGA_DIR
    out_root.mkdir(parents=True, exist_ok=True)

    fetcher = ComicFetcher(delay=args.delay, headless=not args.headful)
    try:
        # 1. 定位漫画
        clue = args.name or f"#{args.comic}"
        if args.comic:
            comic_id = int(args.comic)
        else:
            hits = fetcher.search(args.name)
            hit = _pick_hit(hits, args.name)
            for h in hits:
                print(f"  候选 {h.id:>5}  {h.title[:30]}  作者 {h.author}")
            print(f"  选定 #{hit.id}  {hit.title}")
            comic_id = hit.id

        # 2. 目录
        comic = fetcher.get_catalog(comic_id)
        if not comic.volumes:
            raise ComicError(f"漫画 {comic.id} 无任何卷/章节")
        print(f"\n《{comic.title}》 作者 {comic.author}  共 {len(comic.volumes)} 卷")

        vol_idxs = _parse_vol_spec(args.vol, len(comic.volumes))
        total_ch = sum(len(comic.volumes[vi - 1].chapters) for vi in vol_idxs)
        emit(observer, DownloadEvent(
            "download_started", total=total_ch,
            message=f"准备下载 {len(vol_idxs)} 卷、{total_ch} 章"))
        done_global = 0
        for vi in vol_idxs:
            vol = comic.volumes[vi - 1]
            cancelled, done = _download_volume(
                fetcher, comic, vol, out_root,
                observer=observer, cancel_event=cancel_event)
            done_global += done
            if cancelled:
                return 130
        emit(observer, DownloadEvent(
            "finished", completed=done_global, total=total_ch, message="下载结束。"))

        print("\n全部完成。")
        return 0
    finally:
        fetcher.close()


def _download_volume(fetcher: ComicFetcher, comic: Comic, vol: Volume, out_root: Path,
                     *, observer=None, cancel_event=None) -> tuple[bool, int]:
    title = _safe_name(comic.title)
    vol_name = _safe_name(vol.title or f"第{vol.index}卷")
    vol_dir = out_root / title
    vol_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = vol_dir / f"{title} 第{vol.index:02d}卷.pdf"

    print(f"\n=== {vol_name}（{len(vol.chapters)} 章）===")
    pages: list[bytes] = []
    any_scrambled = False
    failed: list[int] = []
    total_ch = len(vol.chapters)
    done = 0
    for i, ch in enumerate(vol.chapters, start=1):
        # 取消边界在每章开头检查；图片逐张下载途中不可中断（与小说一致）。
        if cancel_event is not None and cancel_event.is_set():
            emit(observer, DownloadEvent(
                "cancelled", volume_index=vol.index, volume_title=vol.title,
                completed=done, total=total_ch, message="已在章节边界安全取消下载。"))
            return True, done
        emit(observer, DownloadEvent(
            "chapter_pending", volume_index=vol.index, volume_title=vol.title,
            chapter_id=str(ch.id), chapter_title=ch.title, completed=done, total=total_ch))
        emit(observer, DownloadEvent(
            "chapter_started", volume_index=vol.index, volume_title=vol.title,
            chapter_id=str(ch.id), chapter_title=ch.title, completed=done, total=total_ch))
        # 每章一个进度条，细粒度到图片逐张下载；本章完成后打满，让用户明确“本章已下完”
        bar = Progress()
        text = f"  [{i}/{total_ch}] {ch.title[:12]}"

        def on_pg(done_pg, tot):
            bar.update(done_pg, tot, text)

        try:
            jpegs, monotonic = fetcher.fetch_chapter_images(comic.id, ch, progress=on_pg)
        except Exception as e:
            # 单章失败不再打断整卷：跳过该章、记录并继续其余章节，最后仍合成 PDF。
            # （KeyboardInterrupt 是 BaseException 不会被此捕获，仍能向上中断整个程序。）
            bar.finish()
            print(f"  {ch.title[:24]} 下载失败：{e}（跳过，继续其余章节）")
            failed.append(ch.id)
            emit(observer, DownloadEvent(
                "chapter_failed", volume_index=vol.index, volume_title=vol.title,
                chapter_id=str(ch.id), chapter_title=ch.title, completed=done, total=total_ch,
                message=str(e)))
            continue
        bar.update(len(jpegs), len(jpegs), text)  # 打满到 100%，表示本章完成
        bar.finish()
        if not monotonic:
            any_scrambled = True
        pages.extend(jpegs)
        done += 1
        emit(observer, DownloadEvent(
            "chapter_finished", volume_index=vol.index, volume_title=vol.title,
            chapter_id=str(ch.id), chapter_title=ch.title, completed=done, total=total_ch))

    if not pages:
        print(f"  {vol_name} 任何章节都未下到，无内容可合成，跳过")
        return False, done

    assemble_pdf(pages, str(pdf_path))
    n = len(pages)
    print(f"  -> 已生成 {pdf_path}（{n} 页）")
    emit(observer, DownloadEvent(
        "pdf_written", volume_index=vol.index, volume_title=vol.title,
        completed=done, total=total_ch, output_path=str(pdf_path),
        message=f"已生成：{pdf_path}（{n} 页）"))
    if failed:
        print(f"  [警示] 有 {len(failed)} 章失败、未含进 PDF：{', '.join(map(str, failed))}；"
              f"可在网络稳定后重跑该卷补齐。")
    if any_scrambled:
        print("  [警示] 本卷存在图片 id 非递增，已按 id 重排；请抽验真序。")
    return False, done


if __name__ == "__main__":
    sys.exit(main())
