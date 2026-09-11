"""项目自有运行目录。

路径从本模块位置推导，因此整个项目移动或克隆到其他目录后仍可运行。
"""

import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "_tmp_dl"
DEFAULT_DOWNLOAD_DIR = PROJECT_ROOT / "download"
# 分类子目录：小说与漫画分开存放，便于各自整理/打包。漫画目录见 comic.cli.MANGA_DIR。
NOVEL_DIR = DEFAULT_DOWNLOAD_DIR / "小说"


# —— 缓存里「本程序自己产出」的那几类 ——
#
# 退出清理**只**动这三类（见 clear_cache）：_tmp_dl 整目录都在 .gitignore 里，删掉不可
# 恢复，而里面除了程序写的缓存，还常有开发期手工放进去的站点侦察脚本、运行日志。
# 白名单比「整棵删掉」安全，代价是将来往缓存目录写新类型的临时文件时，要记得加到这里；
# tests/test_cache_cleanup.py 会比对 C# 侧的同名清单，防止两边各改各的。
IMAGE_CACHE_DIR_NAME = "images"       # downloader.py 逐章插图暂存
VOLUME_PAGE_GLOB = "vol_*_page.html"  # main.py 卷页 HTML 缓存
TEMP_EPUB_GLOB = "*.epub.tmp"         # epub_builder.py 合成中途的临时文件


def _cached_paths():
    """逐个产出缓存目录里本程序写的条目；目录不存在时什么都不产出。"""
    if not CACHE_DIR.is_dir():
        return
    for pattern in (VOLUME_PAGE_GLOB, TEMP_EPUB_GLOB):
        yield from CACHE_DIR.glob(pattern)
    images = CACHE_DIR / IMAGE_CACHE_DIR_NAME
    if images.is_dir():
        yield from (path for path in images.rglob("*") if path.is_file())


def cache_usage():
    """返回缓存目录里**本程序产出**部分的 (文件数, 字节数)；目录不存在时返回 (0, 0)。

    口径与 clear_cache 严格一致：报一个数、实际清掉另一个数，是最难查的那种不一致。
    """
    files = 0
    total = 0
    for path in _cached_paths():
        try:
            files += 1
            total += path.stat().st_size
        except OSError:
            # 正在被别的进程占用/删除：跳过它，统计不完整也不影响清理本身。
            continue
    return files, total


def clear_cache():
    """删除本程序产出的缓存内容，返回清理前的 (文件数, 字节数)。

    只动 IMAGE_CACHE_DIR_NAME / VOLUME_PAGE_GLOB / TEMP_EPUB_GLOB 三类——它们全是可再
    生的中间产物，删掉**不影响** download/ 下的成品，下次重新抓取即可。目录里其它文件
    （开发期手工放的侦察脚本、日志等）一律不碰。

    故这里是纯 best-effort：被占用或无权限的条目留给下次，绝不能让清理失败挡住调用方
    （WPF 关窗）的流程。
    """
    files, total = cache_usage()

    for pattern in (VOLUME_PAGE_GLOB, TEMP_EPUB_GLOB):
        for stale in CACHE_DIR.glob(pattern):
            try:
                stale.unlink()
            except OSError:
                pass

    try:
        shutil.rmtree(CACHE_DIR / IMAGE_CACHE_DIR_NAME, ignore_errors=True)
    except OSError:
        pass

    # 缓存清空后目录本身也顺手收掉；里面还留着非缓存文件时 rmdir 会因非空失败，正好保留。
    try:
        CACHE_DIR.rmdir()
    except OSError:
        pass

    return files, total
