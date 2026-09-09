"""项目自有运行目录。

路径从本模块位置推导，因此整个项目移动或克隆到其他目录后仍可运行。
"""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "_tmp_dl"
DEFAULT_DOWNLOAD_DIR = PROJECT_ROOT / "download"
# 分类子目录：小说与漫画分开存放，便于各自整理/打包。漫画目录见 comic.cli.MANGA_DIR。
NOVEL_DIR = DEFAULT_DOWNLOAD_DIR / "小说"
