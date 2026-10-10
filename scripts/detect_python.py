#!/usr/bin/env python3
"""detect_python 报告 Python 代码中违反静态扫描规范的位置。

用法:
    python detect_python.py <路径...> [--max-line N] [--ccn-threshold N]
                             [--rules a,b,c] [--ccn-only] [--no-pylint] [--json]

输出每行一条: <规范规则名>\\t<文件>:<行>\\t<说明>
退出码: 0 = 无违规, 1 = 有违规, 2 = 自身错误(含路径下没有 .py 文件)

规则名用本 skill 的规范口径(见 references/standards-registry.md),不用 pylint 的
消息 ID——pylint 3.x 把 broad-except 改名为 broad-exception-caught(W0718),
2.x 叫 broad-except(W0703),写死任何一个都会在换版本时失效。这里统一归一化。

本工具只检测,不修改代码。修法见 references/lint-remediation.md。
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _common import (Finding, NoSourceFiles, collect_files,  # noqa: E402
                     finish, read_source)

# ---------------------------------------------------------------------------
# 规范口径的规则名 -> pylint 各版本可能的消息 ID / 符号名
# ---------------------------------------------------------------------------
PYLINT_ALIASES = {
    "broad-except": {"broad-except", "broad-exception-caught", "W0703", "W0718"},
    "line-too-long": {"line-too-long", "C0301"},
    "unused-variable": {"unused-variable", "W0612"},
    "unused-import": {"unused-import", "W0611"},
}

# 反向索引:pylint 符号名 -> 规范规则名
_SYMBOL_TO_RULE = {
    sym: rule for rule, syms in PYLINT_ALIASES.items() for sym in syms
}

DECISION_NODES = (
    ast.If,          # elif 是嵌套 ast.If,自动计入
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ExceptHandler,
    ast.Assert,
    ast.IfExp,
    ast.comprehension,
)
FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)




# ---------------------------------------------------------------------------
# 内置 AST 检测(不依赖 pylint,作为回退与补充)
# ---------------------------------------------------------------------------

def _own_complexity(fn: ast.AST) -> int:
    """函数自身的 CCN,不含嵌套函数(嵌套函数单独成行)。"""
    total = 1

    def visit(node: ast.AST) -> None:
        nonlocal total
        for child in ast.iter_child_nodes(node):
            if isinstance(child, FUNCTION_NODES) and child is not fn:
                continue
            if isinstance(child, DECISION_NODES):
                total += 1
            elif isinstance(child, ast.BoolOp):
                total += len(child.values) - 1
            visit(child)

    visit(fn)
    return total


def _qualname(tree: ast.AST, fn: ast.AST) -> str:
    parts = [getattr(fn, "name", "<lambda>")]
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any(c is fn for c in ast.walk(node)):
            parts.insert(0, node.name)
            break
    return ".".join(parts)


def ast_findings(path: str, max_line: int, ccn_threshold: int) -> list[Finding]:
    """不调用 pylint 的可检测项:行宽、CCN。"""
    try:
        source = read_source(path)
    except OSError as exc:
        print(f"detect_python: 跳过 {path}: {exc}", file=sys.stderr)
        # 读不了的文件不能悄悄不计入:否则退出码仍是 0,门禁把"没查"当成
        # "查过了没问题"——和 NoSourceFiles 要防的是同一类静默通过。
        return [Finding("error", path, 0, f"无法读取: {exc}")]

    out: list[Finding] = []
    # 行宽按字符数,不用按字节计数的工具(UTF-8 中文占 3 字节会误判)。
    for lineno, line in enumerate(source.splitlines(), start=1):
        if len(line) > max_line:
            out.append(Finding(
                "line-too-long", path, lineno,
                f"line length[{len(line)}] should not exceed {max_line} characters",
            ))

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        print(f"detect_python: 跳过无法解析的 {path}: {exc}", file=sys.stderr)
        # 语法错误的文件同样不能计成"无违规":pylint 在时会报 E0001/F0001,
        # 走 AST 回退时只有这里能发现它。
        return out + [Finding(
            "error", path, exc.lineno or 0, f"无法解析(语法错误): {exc.msg}"
        )]

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            ccn = _own_complexity(node)
            if ccn >= ccn_threshold:
                out.append(Finding(
                    "ccn", path, node.lineno,
                    f"{_qualname(tree, node)} 圈复杂度 {ccn} >= {ccn_threshold}",
                ))
    return out


# ---------------------------------------------------------------------------
# pylint 检测
# ---------------------------------------------------------------------------

_PYLINT_LINE = re.compile(
    # file 段用 .+ 而非 [^:]+:Windows 绝对路径带盘符(C:\...),盘符里的冒号
    # 会让 [^:]+ 在盘符处断掉、整行匹配失败,pylint 的结果被整批静默丢弃。
    # 贪婪 .+ 会回退到最后一处 ":行:列:" 上切分,正是行号列号所在的位置。
    r"^(?P<file>.+):(?P<line>\d+):(?P<col>\d+): "
    r"(?P<code>[A-Z]\d+): (?P<msg>.*?) \((?P<symbol>[a-z0-9-]+)\)\s*$"
)


def parse_pylint_line(raw: str) -> Finding | None:
    """把 pylint 文本输出的一行解析成 Finding;不是规范内的行返回 None。"""
    match = _PYLINT_LINE.match(raw.strip())
    if not match:
        return None
    if match.group("code") in ("F0001", "E0001"):
        # 文件没被分析成功(fatal)或语法错误:报成 error,不能静默计为合规。
        # E0001 的符号名 syntax-error 不在 _SYMBOL_TO_RULE 里,不走这条分支
        # 就会被当成"范围外规则"丢掉——而语法错误恰恰是最该报出来的一类。
        return Finding(
            "error", match.group("file"), int(match.group("line")),
            f"pylint 无法分析该文件: {match.group('msg')}",
        )
    rule = _SYMBOL_TO_RULE.get(match.group("symbol"))
    if rule is None:
        return None  # 不在本 skill 规范范围内的规则
    return Finding(
        rule, match.group("file"), int(match.group("line")),
        match.group("msg"),
    )


def pylint_findings(paths: list[str], max_line: int) -> list[Finding] | None:
    """跑 pylint 并归一化到规范规则名。

    返回 None 表示 pylint 不可用,调用方必须据此退回内置 AST 检测。
    不能返回空列表——那和"pylint 跑通且无违规"无法区分,调用方便不会回退,
    AST 的行宽结果会被整批过滤掉,只剩 CCN(曾因此静默漏报)。

    `--dummy-variables-rgx=^_+$` 是**显式钉住** pylint 4.1.2 的默认值
    (实测 `--generate-rcfile` 输出就是这个)。目的是防止未来版本把默认值改成
    宽泛模式后,`unused_xxx` 之类的死代码被静默跳过。

    `encoding="utf-8", errors="replace"` 不能省。pylint 在 duplicate-code
    (R0801) 报告里会**回显源码行**;默认按本地代码页解码时(简体中文 Windows 是
    cp936),源码里的 UTF-8 中文会让读线程抛 UnicodeDecodeError、`proc.stdout`
    变成 None,调用方随即 AttributeError 整个崩掉。实测:扫一个含中文注释的
    Python 项目必崩,零条明细、退出码 1。

    可用性分两步判:先看这个解释器能不能 import 到 pylint,再看进程起不起得来。
    不能用"退出码非零"当判据——pylint 有违规时退出码本来就不是 0,而它的发现
    可能全都不在本 skill 规则范围内,那时 out 也为空,会被误判成没跑起来。
    """
    if importlib.util.find_spec("pylint") is None:
        print("detect_python: pylint 未安装,改用内置 AST 检测", file=sys.stderr)
        return None

    cmd = [
        sys.executable, "-m", "pylint",
        "--max-line-length", str(max_line),
        "--dummy-variables-rgx", "^_+$",
        "--disable", "missing-module-docstring,missing-function-docstring,"
                     "missing-class-docstring,invalid-name",
        "--output-format", "text",
        *paths,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"detect_python: pylint 不可用({exc}),改用内置 AST 检测", file=sys.stderr)
        return None

    out: list[Finding] = []
    for raw in proc.stdout.splitlines():
        finding = parse_pylint_line(raw)
        if finding is not None:
            out.append(finding)

    return out


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

PY_SUFFIXES = (".py",)


def collect(paths: list[str], max_line: int = 120, ccn_threshold: int = 20,
            rules: set[str] | None = None, use_pylint: bool = True) -> list[Finding]:
    """返回全部违规。rules 为空表示不筛选。

    路径下没有 .py 文件时抛 NoSourceFiles——那意味着本次没有覆盖任何文件,
    和"pylint 跑通且无违规"不是一回事。
    """
    files = collect_files(paths, PY_SUFFIXES)
    if not files:
        raise NoSourceFiles("没有找到 .py 文件")

    findings: list[Finding] = []
    if use_pylint:
        pylint_result = pylint_findings(files, max_line)
        if pylint_result is None:
            # pylint 不可用:退回内置 AST 的行宽与 CCN,不能当成"无违规"。
            use_pylint = False
        else:
            findings.extend(pylint_result)
    for path in files:
        # pylint 已覆盖行宽;CCN 只有 AST 能算,补上。error 也要留下:
        # pylint 在时它自己报 E0001/F0001,不在时只有 AST 这条路径能发现
        # 读不了或语法错误的文件。
        findings.extend(
            f for f in ast_findings(path, max_line, ccn_threshold)
            if not use_pylint or f.rule in ("ccn", "error")
        )

    if rules:
        findings = [f for f in findings if f.rule in rules]
    return findings




def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Python 规范检测")
    parser.add_argument("paths", nargs="+", help="文件或目录")
    parser.add_argument("--max-line", type=int, default=120)
    parser.add_argument("--ccn-threshold", type=int, default=20)
    parser.add_argument("--rules", default="",
                        help="只报告这些规范规则名,逗号分隔")
    parser.add_argument("--no-pylint", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    label = os.path.basename(__file__)[:-3]
    rules = {r.strip() for r in args.rules.split(",") if r.strip()}
    return finish(
        lambda: collect(args.paths, max_line=args.max_line,
                        ccn_threshold=args.ccn_threshold, rules=rules,
                        use_pylint=not args.no_pylint),
        label, args.json)


if __name__ == "__main__":
    sys.exit(main())
