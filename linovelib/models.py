from dataclasses import dataclass, field


@dataclass
class ChapterPage:
    index: int
    url: str
    title: str
    paragraphs: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)


@dataclass
class ImageAsset:
    epub_path: str   # EPUB 内相对路径，如 "images/154932_1.jpg"
    data: bytes


@dataclass
class Chapter:
    id: str
    url: str
    title: str
    pages: list[ChapterPage] = field(default_factory=list)
    html: str = ""                              # 合并后的正文 HTML，供 EPUB 使用
    image_assets: list[ImageAsset] = field(default_factory=list)


@dataclass
class Volume:
    title: str
    chapters: list[Chapter] = field(default_factory=list)
    vid: str = ""          # 卷编号，对应 /novel/{nid}/vol_{vid}.html
    cover_url: str = ""    # 该卷独立封面（来自卷页 og:image）


@dataclass
class Novel:
    id: str
    title: str
    author: str
    cover_url: str = ""
    cover_text: str = ""   # 备用：未拿到封面时的占位文本
    volumes: list[Volume] = field(default_factory=list)
