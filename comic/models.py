"""bilimanga.net（嗶哩漫畫）数据模型。

与小说侧 linovelib.models 遥相呼应，但面向「图片型章节 + PDF 输出」：
- 章节页面直接暴露所有图片的 data-src，无正文文本块；
- 漫画章节图片顺序未经乱序（data-src 图片 id 严格递增，见 _get_chapter_order 的校验），
  因此无需 linovelib 那套逐页逆置换正确性，只需保留 DOM 顺序即可。
"""

from dataclasses import dataclass, field


@dataclass
class ComicHit:
    """一次书名搜索命中。"""
    id: int
    title: str
    author: str
    # 搜索采用「填 #searchkey -> POST /search.html」的 JS 流程，结果从渲染后 DOM 解析。
    url: str = ""
    desc: str = ""


@dataclass
class Chapter:
    id: int                # 章节号 chid，对应 /read/{cid}/{chid}.html
    title: str
    url: str
    image_urls: list[str] = field(default_factory=list)   # 图片 URL，按阅读顺序


@dataclass
class Volume:
    index: int             # 1 起卷序
    title: str
    chapters: list[Chapter] = field(default_factory=list)


@dataclass
class Comic:
    id: int
    title: str
    author: str = ""
    cover_url: str = ""
    volumes: list[Volume] = field(default_factory=list)
