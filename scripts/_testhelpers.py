#!/usr/bin/env python3
"""测试共用的写入与断言助手。

三个 test_detect_*.py 都要"写一个样本源文件、按规则筛 Finding"。这份代码在
三个文件里各抄一份时,pylint 的 R0801 duplicate-code 会报出来——2.1.0 正是用
同样的办法把 detect_go / detect_cpp 的重复从 10+ 处压到 2 处的,测试侧不豁免。

只放测试用的东西;被测代码共用的基础设施在 _common.py,两者不要混。
"""

from __future__ import annotations

import os


def write_source(directory, name: str, body: str,
                 encoding: str = "utf-8") -> str:
    """把一个样本源文件写进 directory,返回路径。

    固定 newline="\\n":scan_lines 按行切分并据行号报告,CRLF 会让 Windows 上
    写出的样本和字面量里的行数对不上,行号断言随即假红。
    """
    path = os.path.join(str(directory), name)
    with open(path, "w", encoding=encoding, newline="\n") as handle:
        handle.write(body)
    return path


def write_bytes(directory, name: str, data: bytes) -> str:
    """按原始字节写一个样本文件,返回路径。

    给"既非 UTF-8 也非 GBK"的编码兜底用例用:那种字节没法用文本模式写。
    """
    path = os.path.join(str(directory), name)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def rules_of(findings) -> set[str]:
    """Finding 列表里出现过的规范规则名。"""
    return {f.rule for f in findings}


def lines_of(findings, rule: str) -> list[int]:
    """某条规则命中的行号,升序。"""
    return sorted(f.line for f in findings if f.rule == rule)
