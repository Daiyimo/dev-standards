#!/usr/bin/env python3
"""detect 按代码规范检测项目源码,是 scripts/ 的唯一入口。

用法:
    python detect.py [路径...] [--lang auto|python|go|cpp] [--json]
                      [--max-line N] [--max-func N] [--ccn-threshold N]
                      [--rules a,b,c]

路径可以是文件或目录;不给路径时默认当前目录。--lang auto 按目录里出现的
源文件后缀自动判别,混合栈项目会把每种语言都跑一遍并合并结果。

输出每行一条: <规范规则名>\\t<文件>:<行>\\t<说明>
退出码: 0 = 无违规, 1 = 有违规, 2 = 自身错误
     (含路径下没有源文件:没查到任何文件不等于合规,门禁要能区分)

本工具只检测,不修改代码。检测之后的整改流程见 SKILL.md 的「执行层」一节,
具体修法见 references/lint-remediation.md。

新增一门语言或一套规范:加一个 detect_<lang>.py 并提供 collect(paths, ...) -> list,
在本文件的 LANG_SUFFIXES 里登记一行即可,不需要改其他地方。
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _common import SKIP_DIRS, NoSourceFiles, report  # noqa: E402

LANG_SUFFIXES = {
    "python": (".py",),
    "go": (".go",),
    "cpp": (".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".inl"),
}

# 规范规则名全集,与 references/standards-registry.md 第 1 节一致。
# `error` 是检测器自报的"读不了/解析失败",不是某条规范,但同样可筛。
KNOWN_RULES = {
    "line-too-long", "tosa/linelength", "broad-except", "unused-variable",
    "unused-import", "ccn", "golint/package", "golint/funccomment",
    "golint/structcomment", "golint/interfacecomment", "golint/varcomment",
    "golint/fnsize", "BackwardNull", "error",
}



def detect_languages(paths: list[str]) -> list[str]:
    """按路径下出现的源文件后缀推断要跑哪些语言。"""
    found: set[str] = set()
    for path in paths:
        candidates = [path] if os.path.isfile(path) else _walk_files(path)
        for filepath in candidates:
            suffix = os.path.splitext(filepath)[1].lower()
            for lang, suffixes in LANG_SUFFIXES.items():
                if suffix in suffixes:
                    found.add(lang)
    return [lang for lang in ("python", "go", "cpp") if lang in found]



def _walk_files(root: str):
    """目录下全部文件(跳过 SKIP_DIRS),供语言判别用。"""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            yield os.path.join(dirpath, name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按代码规范检测项目源码")
    parser.add_argument("paths", nargs="*", default=["."])
    parser.add_argument("--lang", default="auto",
                        choices=["auto", "python", "go", "cpp"])
    parser.add_argument("--max-line", type=int, default=120)
    parser.add_argument("--max-func", type=int, default=80)
    parser.add_argument("--ccn-threshold", type=int, default=20)
    parser.add_argument("--rules", default="",
                        help="只报告这些规范规则名,逗号分隔")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.lang == "auto":
        langs = detect_languages(args.paths)
        if not langs:
            print("detect: 在给定路径下没找到 Python / Go / C++ 源文件",
                  file=sys.stderr)
            return 2
    else:
        langs = [args.lang]

    wanted = {r.strip() for r in args.rules.split(",") if r.strip()}
    # 规则名打错必须报自身错误。否则"筛掉之后没剩"和"本来没违规"返回同一个 0,
    # 第 13 节第 4 步"修完复检这一类"会因一个拼写错误变成假绿——和
    # NoSourceFiles 要防的是同一类静默通过,只是触发条件从路径打错换成规则名打错。
    unknown = wanted - KNOWN_RULES
    if unknown:
        print("detect: 未知规则名 " + ", ".join(sorted(unknown)) +
              ";可用规则名见 references/standards-registry.md 第 1 节",
              file=sys.stderr)
        return 2

    findings = []
    for lang in langs:
        module = __import__(f"detect_{lang}")
        kwargs = {"max_line": args.max_line}
        if lang == "python":
            kwargs.update(ccn_threshold=args.ccn_threshold)
        else:
            kwargs.update(max_func=args.max_func)
        try:
            findings.extend(module.collect(args.paths, **kwargs))
        except NoSourceFiles as exc:
            # auto 模式是按后缀选出这门语言的,理论上必有文件;真出现说明两次
            # 遍历之间文件消失了。不能静默跳过——那又是一次"没查"报成"没问题"。
            print(f"detect: {lang}: {exc}", file=sys.stderr)
            return 2

    if wanted:
        findings = [f for f in findings if f.rule in wanted]

    return report(findings, args.json)


if __name__ == "__main__":
    sys.exit(main())
