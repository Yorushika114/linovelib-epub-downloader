"""轻量进度条（无第三方依赖）。

在真实终端（isatty=True）用 \\r 原地刷新，直观看到下载进度；
重定向/后台（isatty=False）时改为整行打印、每 10% 一行，避免刷屏。

编码兜底：GBK 等窄代码页的管道/控制台无法输出 █（U+2588）/░（U+2591）时，
自动退化为 ASCII（# 填充、- 空），确保进度条渲染绝不抛 UnicodeEncodeError，
从而不会因为“进度条画不出来”而把一个本可下完的章节误判成失败。
"""

from __future__ import annotations

import sys

_BAR_FULL = "█"
_BAR_EMPTY = "░"
_FALLBACK_FULL = "#"
_FALLBACK_EMPTY = "-"


def _can_encode(ch: str) -> bool:
    enc = getattr(sys.stdout, "encoding", None)
    if not enc:
        return True
    try:
        ch.encode(enc)
        return True
    except UnicodeEncodeError:
        return False


class Progress:
    def __init__(self, width: int = 30):
        self.width = width
        self._tty = sys.stdout.isatty()
        # 选一个本进程 stdout 真正能编码的“格子”字符；不能就退化为 ASCII。
        # （实测 GBK 能编码 █、不能编码 ░，故这里分开探测两种字符。）
        self._filled = _BAR_FULL if _can_encode(_BAR_FULL) else _FALLBACK_FULL
        self._empty = _BAR_EMPTY if _can_encode(_BAR_EMPTY) else _FALLBACK_EMPTY
        self._last_pct = -1
        self.active = False

    def update(self, done: int, total: int, text: str = "") -> None:
        """刷新一次进度。done/total 为当前已下载/总数；tty 下原地刷新。"""
        if total <= 0:
            total = 1
        pct = min(100, done / total * 100)
        filled = int(pct / 100 * self.width)
        bar = self._filled * filled + self._empty * (self.width - filled)
        line = f"{text} [{bar}] {done}/{total} {pct:.0f}%"

        if self._tty:
            # 仅百分比变化才刷新，减少闪烁
            if pct == self._last_pct:
                return
            self._last_pct = pct
            self._write("\r" + line + "\033[K")
        else:
            # 非 tty：每 ≥10% 或到 100% 打印一行完整进度
            if pct - self._last_pct >= 10 or pct >= 100:
                self._last_pct = pct
                self._print_line(line)

    def finish(self, summary: str = "") -> None:
        """收尾：tty 下换行结束原地刷新；summary 非空则打印一行。"""
        if self._tty:
            self._write("\n")
        if summary:
            self._print_line(summary)

    def newline(self) -> None:
        """需要保留进度行、再打印普通输出时调用（tty 下先换行避免覆盖）。"""
        if self._tty:
            self._write("\n")

    # ---- 写出的最终兜底：任何情况下都别因字符编码抛异常打断下载 ----
    def _write(self, s: str) -> None:
        try:
            sys.stdout.write(s)
            sys.stdout.flush()
        except UnicodeEncodeError:
            try:
                sys.stdout.write(s.replace(_BAR_FULL, _FALLBACK_FULL)
                                  .replace(_BAR_EMPTY, _FALLBACK_EMPTY))
                sys.stdout.flush()
            except Exception:
                pass

    def _print_line(self, s: str) -> None:
        try:
            print(s)
        except UnicodeEncodeError:
            try:
                print(s.replace(_BAR_FULL, _FALLBACK_FULL)
                      .replace(_BAR_EMPTY, _FALLBACK_EMPTY))
            except Exception:
                pass
