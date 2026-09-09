"""bilimanga.net（嗶哩漫畫）漫画抓取并合成 PDF。

定位方式与小说一致：支持编号与书名两种。WPF 双模式界面与 launcher 菜单均复用本包；
其 fetch 依赖 Playwright（懒加载），故仅在进入漫画路径时才启动浏览器，不影响小说下载链。
"""

from .fetcher import (ComicBlockedError, ComicError, ComicFetcher)
from .models import Comic, ComicHit, Chapter, Volume

__all__ = [
    "Comic", "ComicHit", "Chapter", "Volume",
    "ComicError", "ComicBlockedError", "ComicFetcher",
]
