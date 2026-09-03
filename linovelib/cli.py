import argparse


def choose_volumes(volumes, selected):
    if selected == "all":
        return list(volumes)
    order = list(selected)
    result = []
    for idx in order:
        if idx < 1 or idx > len(volumes):
            raise ValueError(f"卷号 {idx} 超出范围（共有 {len(volumes)} 卷）")
        result.append(volumes[idx - 1])
    return result


def build_parsed_args(argv):
    p = argparse.ArgumentParser(prog="linovelib", description="linovelib 小说下载并生成 EPUB")
    p.add_argument("--novel", help="小说编号，如 3095")
    p.add_argument("--name", help="小说书名（站点搜索优先，失败会提示改用编号）")
    p.add_argument("--volumes", help="选择卷（从 1 开始；支持逗号与 '-' 区间，"
                                     "如 1,3,5 或 1-3,5 或 1-3,6-8）")
    p.add_argument("--vol", dest="volumes_short", choices=["all"], help="下载全部卷")
    p.add_argument("--out", help="输出 epub 路径。默认每下一卷就合成该卷到 "
                                 "download/<小说标题>/；多卷可选再合并整本")
    p.add_argument("--delay", type=float, default=0.4, help="请求间隔秒（默认 0.4）")
    p.add_argument("--no-interactive", action="store_true",
                   help="未指定 --vol/--volumes 时，不弹交互并默认下载全部卷")
    p.add_argument("--merge", action="store_true",
                   help="多卷时额外生成一份「整本合并」的 EPUB（默认逐卷边下边出；不再询问）")
    p.add_argument("--force", action="store_true",
                   help="目标 EPUB 已存在时仍重新下载并覆盖（默认检测到已存在则跳过，所有下载方式均适用）")
    p.add_argument("--reference",
                   help="正版参考 EPUB 路径（仅作【下载后的外部顺序校正/核对】用，不参与下载过程，"
                        "不自动生效、不跨卷）。下载本身始终为参考无关的 Fisher-Yates 反洗牌。")
    return p.parse_args(argv)
