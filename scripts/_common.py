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
        payload = json.dumps([f.as_dict() for f in findings],
                             ensure_ascii=False, indent=2)
        # JSON 是给机器读的,必须按 UTF-8 落盘。print() 在 Windows 上按本地代码页
        # (简体中文环境 cp936)编码,重定向出的字节不是合法 UTF-8,消费端
        # json.loads 直接 UnicodeDecodeError——比乱码更糟,管道整个断掉。
        # 只改 JSON 这一条路:文本明细仍走 print(),GBK 控制台才显示得对。
        # (2.1.3 明确判断过不强制把 stdout 全局改成 UTF-8,那是拿交互场景换管道场景。)
        try:
            sys.stdout.buffer.write(payload.encode("utf-8") + b"\n")
            sys.stdout.buffer.flush()
        except AttributeError:
            # stdout 被换成没有 .buffer 的对象(测试替身、被 wrapper 包住的输出)。
            # 这时候写不出字节,只能退回文本层;但那样就回到了本地代码页,所以必须
            # 留一行 stderr 说明,不能让"保证 UTF-8"静默降级。
            print("detect: stdout 没有 buffer,--json 无法保证 UTF-8 输出",
                  file=sys.stderr)
            print(payload)
        except (BrokenPipeError, OSError, ValueError):
            # 读端已经走了(detect.py --json | head)。这不是我们的输出出错,
            # 不该吐 traceback;按原退出码结束即可。Python 退出时还会再刷一次
            # stdout,那时再报一次 BrokenPipeError 会被解释器吞掉并打
            # "Exception ignored" —— 先把 fd 指向 devnull 避开它。
            try:
                os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
            except (OSError, ValueError):
                pass
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
    except Exception as exc:  # pylint: disable=broad-except
        # 这是顶层兜底:检测器自身崩了(解析器缺陷、没预料到的文件形态)时,
        # 必须和"发现违规"区分开——否则一个崩掉的检测器和一份 5000 条的违规
        # 清单返回同一个退出码 1,门禁把"没查完"当成"查过了"。
        # 不收窄:这里正是"任何异常都只应记为检测失败,不是让 traceback 冲到
        # 调用方面前"的边界,具体异常面无法预判。
        print(f"{label}: 检测器自身出错({type(exc).__name__}: {exc})",
              file=sys.stderr)
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

def _cpp_raw_string_open(line: str, i: int) -> tuple[str, int] | None:
    """位置 i 上是否是 C++11 raw string 的起点 `R"delim(`。

    是则返回 (收尾符 `)delim"`, 起点消耗的字符数);不是则返回 None。
    delim 按标准不允许含圆括号、反斜杠和空白,长度上限 16——超长的按普通字符串处理。
    """
    if line[i] != "R" or i + 1 >= len(line) or line[i + 1] != '"':
        return None
    j = i + 2
    delim: list[str] = []
    while j < len(line) and line[j] != "(" and len(delim) < 16:
        delim.append(line[j])
        j += 1
    if j >= len(line) or line[j] != "(":
        return None
    text = "".join(delim)
    if any(c in text for c in '()\\ \t'):
        return None
    return ")" + text + '"', (j + 1) - i


def scan_lines(text: str, raw_strings: bool = True) -> list[dict]:
    """把每行切成 {raw, code, depth_before, depth_after, is_comment}。

    code 是去掉注释与字符串内容后的代码(字符串整体替换为 ""),关键是其中的
    花括号不参与深度计数。跨行块注释按状态延续。

    raw_strings=False 时忽略反引号裸字符串(C/C++ 没有这个语法;Go 有),
    但**启用 C++11 raw string** `R"delim( ... )delim"`。

    跨行延续的状态有三类,都必须在行间传递,否则花括号深度会失真:
    块注释、Go 反引号裸串、C++ raw string,以及**行尾是反斜杠续行的字符串**。
    最后一条只在 `行尾是反斜杠` 时延续——落单的引号不延续,否则 C++14 的数字
    分隔符 `1'000'000` 会把后半文件都吞掉。

    圈复杂度约 32,高于常规阈值但**不拆**:复杂度来自词法状态本身
    (行注释 / 块注释 / 字符串 / 裸字符串 / raw string / 转义各一支),
    不是为了少写代码堆出来的。拆成多个函数只会让状态在函数间传递,
    比现在更难读,也不降低出错概率。
    """
    rows: list[dict] = []
    depth = 0
    in_block_comment = False
    in_raw_string = False
    in_cpp_raw = False          # C++11 R"delim( ... )delim"
    cpp_raw_end = ""            # 对应的收尾符 )delim"
    in_string = False
    quote = ""

    for raw in split_lines(text):
        code_chars: list[str] = []
        i, n = 0, len(raw)
        in_line_comment = False

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

            if in_cpp_raw:
                # raw string 里的内容一律不当代码:花括号、引号、注释全都不是
                if raw.startswith(cpp_raw_end, i):
                    in_cpp_raw = False
                    code_chars.append('""')
                    i += len(cpp_raw_end)
                else:
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
            if not raw_strings:
                opened = _cpp_raw_string_open(raw, i)
                if opened is not None:
                    cpp_raw_end, skip = opened
                    in_cpp_raw = True
                    i += skip
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

        # 字符串状态只在"行尾是反斜杠续行"时带到下一行。落单的引号就地复位:
        # 1'000'000 里的撇号、没配对的引号都不该把后半文件吞掉。
        if not (in_string and raw.endswith("\\")):
            in_string = False
            quote = ""

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


# 这些字符 `str.splitlines()` 也当成行尾,但 pylint 不认——源码里它们是合法内容
# (换页符、行分隔符)。口径和 pylint 的 `specific_splitlines` 逐字对齐。
_UNSPLIT_ENDS = {"\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"}


def split_lines(text: str) -> list[str]:
    """按行切分文本，行尾只认 `\\n` / `\\r` / `\\r\\n`。

    不能用 `str.splitlines()`：它还会在 `\\x0b` `\\x0c` `\\x1c` `\\x1d` `\\x1e`
    `\\x85` U+2028 U+2029 处断行。这些字符在源码里是合法内容，一旦出现就多数一行，
    其后**所有行号整体偏移**——实测一条真实在第 3 行的 133 字符行被报到第 4 行，
    而 pylint 报第 3 行。这里照抄 pylint `specific_splitlines` 的算法（先把
    `splitlines(True)` 出来的、以上述字符结尾的行拼回下一行），两条路径的行号才一致。

    与 pylint 的唯一差别：文本以未拼接字符结尾时，pylint 把残留 buffer 丢掉，这里
    保留——多扫一行只是多一次检查，少一行可能静默漏报。

    返回的行不含行尾符（`scan_lines` 的 `code` 不能带换行）。
    """
    res: list[str] = []
    buffer = ""
    for atomic in text.splitlines(True):
        if atomic[-1] in _UNSPLIT_ENDS:
            buffer += atomic
        else:
            res.append(buffer + atomic)
            buffer = ""
    if buffer:
        res.append(buffer)
    return [line.rstrip("\r\n") for line in res]


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


def _has_sources_directly(directory: str, suffixes: tuple[str, ...]) -> bool:
    """目录**本层**有没有目标后缀的文件（不递归）。

    只查一层是刻意的：`build/` / `node_modules/` 里可能有几十万个文件，
    为了报一句提示而完整遍历一遍，比不提示更贵。嵌套很深的源码被跳过时这条
    提示不出，但那种情况下连"有多少"都数不清，提示了也没有可操作性。
    """
    try:
        with os.scandir(directory) as entries:
            return any(e.is_file() and e.name.endswith(suffixes)
                       for e in entries)
    except OSError:
        return False


def collect_files(paths: list[str], suffixes: tuple[str, ...],
                  skip_dirs: set[str] | None = None) -> list[str]:
    """按后缀收集源文件。paths 里的文件直接收,目录递归。

    跳过的目录里**确实有源文件**时,往 stderr 打一行汇总。没有这一行,
    "少扫一批文件而退出码不变"——门禁把没查当成查过了,正是 NoSourceFiles
    要防的那类静默通过,只是触发条件从路径打错换成目录名。
    """
    # 用 is None 判,不用 `or`:`skip_dirs=set()` 是 falsy,`or` 会让它静默回落成
    # 默认 SKIP_DIRS,"我想什么都不跳过"这个意图表达不出来。当前没有调用方传空集合,
    # 但这是个踩上去才知道的坑。
    skip = SKIP_DIRS if skip_dirs is None else skip_dirs
    out: list[str] = []
    skipped: set[str] = set()
    for path in paths:
        if os.path.isfile(path):
            out.append(path)
            continue
        for dirpath, dirnames, filenames in os.walk(path):
            for name in dirnames:
                if name in skip:
                    full = os.path.join(dirpath, name)
                    if _has_sources_directly(full, suffixes):
                        skipped.add(full)
            dirnames[:] = [d for d in dirnames if d not in skip]
            for name in filenames:
                if name.endswith(suffixes):
                    out.append(os.path.join(dirpath, name))
    if skipped:
        shown = ", ".join(sorted(skipped)[:3])
        more = f" 等 {len(skipped)} 个" if len(skipped) > 3 else ""
        print(f"detect: 跳过的目录里有源文件,未纳入检测:{shown}{more}"
              f"(见 README 已知缺陷「SKIP_DIRS」)", file=sys.stderr)
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
