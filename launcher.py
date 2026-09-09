import sys
from main import main, _resolve_identifier
from linovelib.resolver import ResolveError

# ===== 顶层类型菜单：小说 / 漫画 =====
# 回退语义（两级）：
#   - b/back/返回 = 直接回主菜单（跳到最外层重选类型）
#   - q/上一步     = 返回上一步（撤销当前输入）；走到"编号/书名"这层时，其上一步即主菜单。
#   主菜单的 q/回车 才真正退出程序。
_MENU_BACK_TOKENS = {"b", "back", "quit", "exit", "返回", "返"}
_STEP_BACK_TOKENS = {"q", "上一步", "返回上一步", "退一步", "上一个"}


def _prompt(label):
    try:
        return input(label).strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def _is_menu_back(text):
    return (text or "").strip().lower() in _MENU_BACK_TOKENS


def _is_step_back(text):
    return (text or "").strip().lower() in _STEP_BACK_TOKENS


def _is_blank(text):
    return not (text or "").strip()


def _menu():
    while True:
        print("\n" + "=" * 58)
        print("  轻小说/漫画 下载器 —— 请选择类型：")
        print("    1  下载小说")
        print("    2  下载漫画")
        print("    q  退出")
        c = _prompt("请输入 1 或 2（q 退出）: ").strip().lower()
        if c == "1":
            _novel_loop()
        elif c == "2":
            _manga_loop()
        elif c in ("q", "quit", "exit") or not c:
            print("已退出。")
            return 0
        else:
            print("无效选择，请输入 1 或 2。")


# ===== 小说循环（原有逻辑抽出，改回退：回车/b 返主菜单；卷数回车=全部）=====
def _novel_loop():
    print("循环爬取模式：输入小说编号（或书名）与卷数即可连续下载。q=返回上一步，b=返回主菜单。")
    print("示例：编号 3095  卷 4 ；书名会搜索解析出编号；卷数支持 1-3,5 区间，all 下载全部，回车=全部。")
    print("默认每下一卷就合成一本（边下边出）；不再询问是否合并。")
    while True:
        print("\n" + "=" * 58)
        nid = _prompt("请输入小说编号或书名(编号如 3095，书名如 败北女角太多了；q 上一步 / b 主菜单 / 回车返回主菜单): ")
        # 编号/书名 这层是小说内部最外层输入，q/b/回车 之上只有主菜单
        if _is_menu_back(nid) or _is_step_back(nid) or _is_blank(nid):
            print("返回主菜单。")
            return

        # 书名：先在解析阶段筛出确定编号（多候选时在此让用户选择），再进入卷数选择；
        # 编号：直达卷数选择，不启动浏览器。
        if not nid.isdigit():
            print("\n书名搜索解析中……")
            print()
            try:
                nid = _resolve_identifier(nid)
            except ResolveError as e:
                print(e)
                continue
            print(f"已选定小说编号：{nid}")
            print()

        vol = _prompt("请输入卷数(多个用逗号分隔，支持区间如 1-3,5；全部输入 all，回车=全部；q 上一步 / b 主菜单): ")
        if _is_step_back(vol):
            print("返回上一步（重新选择小说）。")
            continue  # 回 编号/书名 层
        if _is_menu_back(vol):
            print("返回主菜单。")
            return
        if _is_blank(vol):
            vol = "all"

        print()
        print(f"开始下载：编号={nid}   卷={vol}")
        print("每完成一章会打印“章节 x/y 完成”。正文将在后台用请求抓取，不会再弹出浏览器窗口。")
        print("-" * 58)
        sys.stdout.flush()

        if vol.strip().lower() == "all":
            argv = ["--novel", nid, "--vol", "all"]
        else:
            argv = ["--novel", nid, "--volumes", vol]

        try:
            rc = main(argv)
        except KeyboardInterrupt:
            print("\n已手动中断本次下载。")
            continue
        except Exception as e:
            print(f"发生异常：{e}")
            continue

        print("-" * 58)
        if rc == 0:
            print("本次下载完成，EPUB 已生成。")
        else:
            print(f"本次下载未完成（返回码 {rc}），请查看上方提示。")
        # 循环：回到顶部，继续下一次输入，不退出


# ===== 漫画循环（懒加载 comic；复用同一个浏览器会话）=====
# 书名候选选择 _ask_choice 的返回值：b 回主菜单 / q 回上一步
_BACK_TO_MENU = object()
_BACK_TO_STEP = object()


def _manga_loop():
    # 懒加载：comic/fetcher.py 顶层 import playwright，若在模块顶层 import comic 会让
    # 小说路径也强制依赖 playwright + Edge。故仅在进入漫画分支时才导入。
    try:
        from comic.cli import MANGA_DIR, _parse_vol_spec, _pick_hit, _download_volume
        from comic.fetcher import ComicError, ComicFetcher
    except Exception as e:
        print(f"漫画功能不可用（需要 playwright + Edge）：{e}")
        print("请先安装：pip install playwright && playwright install msedge")
        return

    print("漫画下载：输入编号或书名与卷数即可连续下载。q=返回上一步（重新选漫画），b=返回主菜单。")
    print("示例：编号 1270  卷 1 ；书名会搜索候选；卷数支持 1-3,5 区间，all 下载全部，回车=全部。")

    fetcher = None

    def _get_fetcher():
        nonlocal fetcher
        if fetcher is None:
            fetcher = ComicFetcher()  # 首次真正用到才启动 Edge（懒）
        return fetcher

    def _close_fetcher():
        nonlocal fetcher
        if fetcher is not None:
            try:
                fetcher.close()
            except Exception:
                pass
            fetcher = None

    def _ask_choice(name, hits):
        """书名候选选择。返回 ComicHit / _BACK_TO_MENU(b) / _BACK_TO_STEP(q)。"""
        print(f"按「{name}」搜到 {len(hits)} 个候选：")
        for i, h in enumerate(hits, start=1):
            print(f"  [{i}] {h.id:<6} {h.title[:30]}  作者 {h.author}")
        raw = _prompt("输入序号（回车选第一条/精确匹配；q 上一步 / b 主菜单）: ")
        if _is_step_back(raw):
            return _BACK_TO_STEP
        if _is_menu_back(raw):
            return _BACK_TO_MENU
        if raw.strip().isdigit():
            i = int(raw.strip())
            if 1 <= i <= len(hits):
                return hits[i - 1]
        return _pick_hit(hits, name)

    try:
        while True:
            print("\n" + "=" * 58)
            clue = _prompt("请输入漫画编号或书名(编号如 1270，书名如 刀劍神域；q 上一步 / b 主菜单 / 回车返回主菜单): ")
            # 编号/书名 这层已是漫画内部最外层输入，q/b/回车 之上只有主菜单
            if _is_menu_back(clue) or _is_step_back(clue) or _is_blank(clue):
                print("返回主菜单。")
                return

            try:
                if clue.isdigit():
                    comic_id = int(clue)
                else:
                    hits = _get_fetcher().search(clue)
                    if not hits:
                        raise ComicError(f"未搜到「{clue}」相关结果，请换关键词或直接用编号。")
                    r = _ask_choice(clue, hits)
                    if r is _BACK_TO_MENU:
                        print("返回主菜单。")
                        return
                    if r is _BACK_TO_STEP:
                        print("返回上一步（重新选择漫画）。")
                        continue  # 回 编号/书名 层
                    print(f"  选定 #{r.id}  {r.title}")
                    comic_id = r.id

                comic = _get_fetcher().get_catalog(comic_id)
                if not comic.volumes:
                    raise ComicError(f"漫画 {comic_id} 无任何卷/章节")
                print(f"\n《{comic.title}》 作者 {comic.author}  共 {len(comic.volumes)} 卷")

                vol_idxs = None
                while True:
                    vol = _prompt("请输入卷数(逗号/区间如 1-3,5；all 全部，回车=全部；q 上一步 / b 主菜单): ")
                    if _is_step_back(vol):
                        print("返回上一步（重新选择漫画）。")
                        break  # vol_idxs 仍为 None → 回 编号/书名 层
                    if _is_menu_back(vol):
                        print("返回主菜单。")
                        return
                    if _is_blank(vol):
                        vol = "all"
                    try:
                        vol_idxs = _parse_vol_spec(vol, len(comic.volumes))
                        break
                    except ValueError as e:
                        print(e)

                if vol_idxs is None:
                    continue  # q 返回上一步 → 回 编号/书名 层
                for vi in vol_idxs:
                    _download_volume(_get_fetcher(), comic, comic.volumes[vi - 1], MANGA_DIR)
                print("\n本次漫画下载完成。")

            except ComicError as e:
                print(e)  # 含封禁/0 结果/无卷；不关浏览器，保留暖机会话
                continue
            except KeyboardInterrupt:
                print("\n已手动中断本次下载。")
                _close_fetcher()  # 中断后浏览器状态未知，下次用新的
                continue
            except Exception as e:
                print(f"发生异常：{e}")
                _close_fetcher()
                continue
    finally:
        _close_fetcher()  # 回主菜单/是否退出都关掉浏览器


if __name__ == "__main__":
    # 用 Win32 API 显式设置窗口标题：SetConsoleTitleW 接收 UTF-16 字符串，
    # 与终端/批处理代码页无关，避免 download.bat 里 UTF-8 中文被 GBK 误读成乱码。
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW("轻小说/漫画 下载器")
    except Exception:
        pass
    sys.exit(_menu())
