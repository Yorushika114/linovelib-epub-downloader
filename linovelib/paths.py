"""项目自有运行目录。

路径从本模块位置推导，因此整个项目移动或克隆到其他目录后仍可运行。
"""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "_tmp_dl"
DEFAULT_DOWNLOAD_DIR = PROJECT_ROOT / "download"
