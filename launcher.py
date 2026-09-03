import sys
from main import main


def _prompt(label):
    try:
        return input(label).strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def _launch():
    print("循环爬取模式：输入小说编号（或书名）与卷数即可连续下载；在编号/书名处输入 q 或直接回车退出循环。")
    print("示例：编号 3095  卷 4 ；书名会搜索解析出编号；卷数支持 1-3,5 区间，all 下载全部。")
    print("默认每下一卷就合成一本（边下边出）；不再询问是否合并。")
    while True:
        print("\n" + "=" * 58)
        nid = _prompt("请输入小说编号或书名(编号如 3095，书名如 败北女角太多了；q/回车退出): ")
        if not nid or nid.strip().lower() in ("q", "quit", "exit"):
            print("已退出循环。")
            return 0

        vol = _prompt("请输入卷数(多个用逗号分隔，支持区间如 1-3,5；全部输入 all): ")
        if not vol:
            print("未输入卷数，已取消本次，请重新输入。")
            continue

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


if __name__ == "__main__":
    sys.exit(_launch())
