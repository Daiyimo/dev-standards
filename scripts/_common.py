#!/usr/bin/env python3
"""各语言检测器共用的基础设施。

单独一个模块,避免 Finding / report / 行宽检查 / 扫描器在 detect_go.py 与
detect_cpp.py 里各抄一份(pylint 的 R0801 duplicate-code 会报出来)。

约定:每个 detect_<lang>.py 提供

    collect(paths, max_line=..., ...) -> list[Finding]
    main(argv=None) -> int                     # 退出码 0/1/2,内部用这里的 report

collect() 在给定路径下没有目标语言源文件时抛 NoSourceFiles;main() 接住后
打一行到 stderr 并返回退出码 2。没查到任何文件不等于"无违规",门禁要能区分
"查过了没问题"和"根本没查"。三个 main() 的这段共有尾部收在 finish() 里,
退出码 2 的契约只实现一次。

新增规则时不需要动本文件;新增语言时也不需要。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable

SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules",
             "vendor", "testdata", "build", "dist"}


class NoSourceFiles(Exception):
    """给定路径下没有目标语言的源文件。

    这不是"无违规":路径打错、或对一门语言用错了 --lang 时,一次扫描其实
    没有覆盖任何文件。返回退出码 0 会让门禁把"没查"当成"查过了没问题",
    这是检测器最危险的一种静默通过。因此 collect() 抛这个异常,
    由 main() 转成退出码 2。
    """


class Finding:
    """一处违规。rule 用本 skill 的规范规则名,不用某个工具的消息 ID。"""

    __slots__ = ("rule", "file", "line", "message")

    def __init__(self, rule: str, file: str, line: int, message: str) -> None:
        self.rule = rule
        self.file = file
        self.line = line
        self.message = message

    def as_dict(self) -> dict:
        return {"rule": self.rule, "file": self.file,
                "line": self.line, "message": self.message}


def report(findings: list[Finding], as_json: bool = False,
           label: str = "detect") -> int:
    """打印结果,返回退出码(0 无违规 / 1 有违规)。

    明细走 stdout(可管道),聚合统计走 stderr(不污染可解析输出)。
    """
    if as_json:
        print(json.dumps([f.as_dict() for f in findings], ensure_ascii=False, indent=2))
        return 1 if findings else 0

    for f in sorted(findings, key=lambda x: (x.rule, x.file, x.line)):
        print(f"{f.rule}\t{f.file}:{f.line}\t{f.message}")

    counts: dict[str, int] = {}
    for f in findings:
        counts[f.rule] = counts.get(f.rule, 0) + 1
    if findings:
        print(f"{label}: {len(findings)} 处违规", file=sys.stderr)
        for rule, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"  {rule:<24} {n}", file=sys.stderr)
    return 1 if findings else 0


def finish(collect: Callable[[], list[Finding]], label: str,
           as_json: bool) -> int:
    """三个语言检测器共用的 main() 尾部。

    接住 NoSourceFiles 并转成退出码 2,再走 report。抽到这一处是因为
    "路径下没源文件"这个契约只该有一个实现——三个入口各抄一份时,改一处
    会漏两处;而且 pylint 的 R0801 会报出来。2.1.0 曾把这类重复从 10+ 处
    压到 2 处,当时判断"各 4 行的尾部不值得抽";现在尾部带上了退出码 2 的
    逻辑、长到 9 行,值得抽了。

    collect 用零参 callable 传进来:三个检测器的参数个数和名字都不同,
    用 **kwargs 透传反而要比调用处抄一遍还长。
    """
    try:
        findings = collect()
    except NoSourceFiles as exc:
        print(f"{label}: {exc}", file=sys.stderr)
        return 2
    return report(findings, as_json, label=label)


def overlong_lines(rows: list[dict], path: str, max_line: int) -> list[Finding]:
    """行宽违规。rows 是 scan_lines() 的产物。

    按**字符数**判断,不用按字节计数的工具——UTF-8 中文占 3 字节,
    awk length / wc 会按字节计,导致含中文的文件多出一批假阳性。
    """
    return [
        Finding(
            "tosa/linelength", path, lineno,
            f"line length[{len(row['raw'])}] should not exceed {max_line} characters",
        )
        for lineno, row in enumerate(rows, start=1)
        if len(row["raw"]) > max_line
    ]


# ---------------------------------------------------------------------------
# C 家族极简扫描器:Go 与 C/C++ 共用
# ---------------------------------------------------------------------------

def scan_lines(text: str, raw_strings: bool = True) -> list[dict]:
    """把每行切成 {raw, code, depth_before, depth_after, is_comment}。

    code 是去掉注释与字符串内容后的代码(字符串整体替换为 ""),关键是其中的
    花括号不参与深度计数。跨行块注释按状态延续。

    raw_strings=False 时忽略反引号裸字符串(C/C++ 没有这个语法;Go 有)。

    圈复杂度约 25,高于常规阈值但**不拆**:复杂度来自词法状态本身
    (行注释 / 块注释 / 字符串 / 裸字符串 / 转义各一支),不是为了少写代码堆出来的。
    拆成多个函数只会让状态在函数间传递,比现在更难读,也不降低出错概率。
    """
    rows: list[dict] = []
    depth = 0
    in_block_comment = False
    in_raw_string = False

    for raw in text.splitlines():
        code_chars: list[str] = []
        i, n = 0, len(raw)
        in_line_comment = False
        in_string = False
        quote = ""

        while i < n:
            ch = raw[i]

            if in_block_comment:
                if ch == "*" and i + 1 < n and raw[i + 1] == "/":
                    in_block_comment = False
                    i += 2
                    continue
                i += 1
                continue

            if in_raw_string:
                if ch == "`":
                    in_raw_string = False
                    code_chars.append('""')
                i += 1
                continue

            if in_line_comment:
                break  # 行注释后续全部丢弃

            if in_string:
                if ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    in_string = False
                    code_chars.append('""')
                i += 1
                continue

            # 不在任何特殊状态
            if ch == "/" and i + 1 < n and raw[i + 1] == "/":
                in_line_comment = True
                break
            if ch == "/" and i + 1 < n and raw[i + 1] == "*":
                in_block_comment = True
                i += 2
                continue
            if raw_strings and ch == "`":
                in_raw_string = True
                i += 1
                continue
            if ch in ('"', "'"):
                in_string = True
                quote = ch
                i += 1
                continue

            code_chars.append(ch)
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth = max(0, depth - 1)
            i += 1

        code = "".join(code_chars)
        rows.append({
            "raw": raw,
            "code": code,
            # depth_after 即扫完本行后的花括号深度;depth_before 由下面回填。
            "depth_before": 0,
            "depth_after": depth,
            "is_comment": not code.strip() and raw.strip().startswith(("//", "/*", "*")),
        })

    prev = 0
    for row in rows:
        row["depth_before"] = prev
        prev = row["depth_after"]
    return rows


def read_source(path: str) -> str:
    """把源文件读成文本,按 UTF-8 → GBK → latin-1 的顺序试。

    不能一律 `encoding="utf-8", errors="replace"` 硬解:GBK 编码的中文每个字
    占 2 字节,两个字节都会被替换成 U+FFFD,行宽按字符数一算就翻倍——实测一条
    真实长度 94 字符的 GBK 行被判成 154 字符并报 `tosa/linelength`。这正是
    `overlong_lines` 说的"中文不按字节误判",只是肇事者从 awk 换成了解码器。

    `utf-8-sig` 顺带吃掉 BOM:带 BOM 的 Go 文件包注释曾因此漏检(`\\ufeff` 不是
    Python 的空白字符,`strip()` 掉不了它)。

    调用方负责接 `OSError`。解码永不失败:latin-1 是单字节全映射。
    """
    with open(path, "rb") as handle:
        data = handle.read()
    for encoding in ("utf-8-sig", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def collect_files(paths: list[str], suffixes: tuple[str, ...],
                  skip_dirs: set[str] | None = None) -> list[str]:
    """按后缀收集源文件。paths 里的文件直接收,目录递归。"""
    skip = skip_dirs or SKIP_DIRS
    out: list[str] = []
    for path in paths:
        if os.path.isfile(path):
            out.append(path)
            continue
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = [d for d in dirnames if d not in skip]
            for name in filenames:
                if name.endswith(suffixes):
                    out.append(os.path.join(dirpath, name))
    return sorted(out)


def build_parser(description: str) -> argparse.ArgumentParser:
    """Go / C++ 检测器共用的命令行参数。

    两者参数完全一致;Python 检测器多几个自有开关,自己构造。
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--max-line", type=int, default=120)
    parser.add_argument("--max-func", type=int, default=80)
    parser.add_argument("--json", action="store_true")
    return parser
