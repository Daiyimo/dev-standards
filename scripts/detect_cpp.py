#!/usr/bin/env python3
"""detect_cpp 报告 C/C++ 代码中违反静态扫描规范的位置。

用法:
    python detect_cpp.py <目录或文件...> [--max-line N] [--max-func N] [--json]

输出每行一条: <规范规则名>\\t<文件>:<行>\\t<说明>
退出码: 0 = 无违规, 1 = 有违规, 2 = 自身错误(含路径下没有 C/C++ 源文件)

覆盖程度(如实说明,不假装全覆盖):

  tosa/linelength   可靠。按字符数计,中文不会被按字节误判。
  golint/fnsize     启发式。按"函数签名首行到配套右花括号"计,含注释与空行。
  BackwardNull      **候选,必须人工确认**。只报"同一函数内某指针被判空后,
                   在其判空分支之外又被解引用"这一种窄模式,宁可漏报不误报。

真正的空指针分析需要 clang-tidy 之类的分析器。项目里有时优先用它;本工具的
价值是不装分析器也能覆盖机械规则,并把 BackwardNull 候选筛到可人工过一遍的量。

本工具只检测,不修改代码。修法见 references/lint-remediation.md。
"""

from __future__ import annotations

# sys.path 垫片必须先于 _common 导入执行,否则同目录兄弟模块找不到。
# pylint: disable=wrong-import-position
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _common import (Finding, NoSourceFiles, build_parser,  # noqa: E402
                     collect_files, finish, overlong_lines, read_source,
                     scan_lines)
import re

RE_NULL_CHECK = re.compile(
    r"\b(?:if|while)\s*\(\s*(?:!\s*)?([A-Za-z_]\w*)\s*(?:==|!=)\s*"
    r"(?:nullptr|NULL|0)\b"
)
RE_NOT_CHECK = re.compile(r"\b(?:if|while)\s*\(\s*!\s*([A-Za-z_]\w*)\s*\)")
# 形参/成员声明行,不是函数定义
RE_NON_FUNC_HINT = re.compile(
    r"^\s*(?:public|private|protected|namespace|class|struct|enum|union|"
    r"typedef|using|friend|return|case|default)\b"
)
RE_CONTROL = re.compile(r"^\s*(?:if|for|while|switch|else|do|try|catch)\b")
# 提前返回惯用法:判空分支以这些语句结束,之后代码受保护
RE_EXIT_STMT = re.compile(r"^\s*(?:return|continue|break|throw|goto|exit)\b")



CPP_SUFFIXES = (".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".inl")


def _signature_start(rows: list[dict], brace_idx: int) -> int:
    """从 '{' 行往回找函数签名的首行。找不到就返回 '{' 行本身。"""
    j = brace_idx
    while j - 1 >= 0 and not rows[j - 1]["code"].strip():
        j -= 1
    start = j
    while start - 1 >= 0:
        prev_code = rows[start - 1]["code"].strip()
        if not prev_code:
            break
        if prev_code.endswith((";", "}", "{", ":")):
            break
        # 预处理指令不是函数签名的一部分。少了这条,#if 0 里的 `if (x) {`
        # 会把签名回溯到 `#if 0` 那一行,_is_function_signature 于是从
        # "#if 0 if (x)" 里切出函数名 `if`——一个叫 if 的伪函数,行号区间
        # 还跨越大半个文件,fnsize 与 BackwardNull 都会跟着失真。
        if prev_code.startswith("#"):
            break
        if RE_NON_FUNC_HINT.match(prev_code) or RE_CONTROL.match(prev_code):
            break
        start -= 1
    return start


def _is_function_signature(sig: str) -> str | None:
    """判断一段签名是否是函数定义;是则返回函数名,否则 None。

    判据:不是控制语句、不是类型/命名空间声明,且以 ')' 结尾
    (允许 const / noexcept / override 后缀)。构造函数初始化列表
    `Foo::Foo() : a_(1)` 同样以 ')' 结尾,自然覆盖。
    """
    text = sig.strip()
    if not text:
        return None
    if RE_CONTROL.match(text) or RE_NON_FUNC_HINT.match(text):
        return None
    if not text.endswith(")"):
        if not re.search(r"\)\s*(?:const|noexcept|override|final)?\s*$", text):
            return None
    paren = text.rfind("(")
    if paren <= 0:
        return None
    match = re.search(r"([A-Za-z_]\w*)\s*$", text[:paren])
    if not match:
        return None
    return match.group(1)


def function_defs(rows: list[dict]) -> list[tuple[str, int, int]]:
    """返回 (函数名, 首行, 末行) 列表,0-based,含端点。

    命名空间 / class / struct 体不是函数,靠 _is_function_signature 排除;
    因此定义在命名空间或类内的函数(花括号深度 > 1)也能被识别。
    """
    defs: list[tuple[str, int, int]] = []
    for idx, row in enumerate(rows):
        if not row["code"].rstrip().endswith("{"):
            continue
        if row["depth_after"] != row["depth_before"] + 1:
            continue
        start = _signature_start(rows, idx)
        sig = " ".join(rows[k]["code"].strip() for k in range(start, idx + 1))
        # 去掉行尾的 '{',否则 _is_function_signature 的"以 ) 结尾"判据不成立
        if sig.rstrip().endswith("{"):
            sig = sig.rstrip()[:-1].rstrip()
        name = _is_function_signature(sig)
        if name is None:
            continue
        end = idx
        while end < len(rows) and rows[end]["depth_after"] != row["depth_before"]:
            end += 1
        # 花括号不配对时(最常见是 #if 0 条件编译)depth 永远回不到基线,
        # end 会一路走到 len(rows)。夹紧到末行:调用方都按
        # range(fstart, fend + 1) 遍历,不夹就会越界——那正是 2.1.4 之前
        # _backward_null_candidates 崩掉的原因。
        defs.append((name, start, min(end, len(rows) - 1)))
    return defs


def detect_file(path: str, max_line: int, max_func: int) -> list[Finding]:
    try:
        text = read_source(path)
    except OSError as exc:
        print(f"detect_cpp: 跳过 {path}: {exc}", file=sys.stderr)
        # 读不了的文件不能悄悄不计入:否则退出码仍是 0,门禁把"没查"当成
        # "查过了没问题"——和 NoSourceFiles 要防的是同一类静默通过。
        return [Finding("error", path, 0, f"无法读取: {exc}")]

    rows = scan_lines(text, raw_strings=False)
    out: list[Finding] = []

    # ---- 行宽(按字符数,见 _common.overlong_lines) ----
    out.extend(overlong_lines(rows, path, max_line))

    # ---- 函数规模 ----
    for name, start, end in function_defs(rows):
        size = (end - start) + 1
        if size > max_func:
            out.append(Finding(
                "golint/fnsize", path, start + 1,
                f"函数 {name} 的行数是 {size},规范设定值为 {max_func}",
            ))

    # ---- BackwardNull 候选 ----
    # 只在单个函数体内分析:收集判空点,再找判空分支之外的解引用。
    out.extend(_backward_null_candidates(path, rows))
    return out


def _backward_null_candidates(path: str, rows: list[dict]) -> list[Finding]:
    """窄模式候选:指针判空后,在判空分支之外被解引用。

    关键精度规则:**判空体以 return/continue/break/throw 结尾时,if 之后的代码
    是被保护的**(提前返回惯用法),保护一直延伸到函数末尾或该指针被重新赋值处。
    不处理这条会把最常见的 guard clause 全报成候选,噪声比信号多。
    """
    out: list[Finding] = []
    # 纯下划线匹配钉住的 --dummy-variables-rgx=^_+$;写成 _name 会被当成
    # 真变量报 unused-variable。
    for _, fstart, fend in function_defs(rows):
        # guarded[name] = 受保护的行区间列表(0-based,含端点)
        guarded: dict[str, list[tuple[int, int]]] = {}
        for idx in range(fstart, fend + 1):
            code = rows[idx]["code"]
            match = RE_NULL_CHECK.search(code) or RE_NOT_CHECK.search(code)
            if not match:
                continue
            name = match.group(1)

            # 定位 if 体: '{' 可能在本行,也可能在紧随的几行之后
            brace_idx = _find_open_brace(rows, idx, fend)
            if brace_idx is None:
                body_lo, body_hi = idx, idx          # 单行 if
            else:
                # close 用 < 而不是 <=:花括号不配对时(最常见是 #if 0 条件
                # 编译,扫描器不懂预处理)depth 永远回不到基线,close 会一路
                # 走到 fend+1,下面 range(body_hi + 1, fend + 1) 随即
                # IndexError 整个崩掉——而 finish() 只接 NoSourceFiles。
                close = brace_idx
                while close < fend and rows[close]["depth_after"] != rows[brace_idx]["depth_before"]:
                    close += 1
                body_lo, body_hi = brace_idx, close   # 含右花括号那一行

            # 保护区从判空行本身开始,不只从函数体开始:多行条件
            # `if (p == nullptr ||\n    p->next == nullptr)` 的续行同样受
            # || / && 短路保护,只保护函数体会把第二行的 p->next 报成候选。
            spans: list[tuple[int, int]] = [(idx, body_hi)]
            # 提前返回惯用法:if 之后到函数末尾(或指针被重新赋值处)都受保护
            if _guard_exits(rows, idx, brace_idx, body_lo, body_hi):
                limit = fend
                for k in range(body_hi + 1, fend + 1):
                    if re.search(rf"\b{re.escape(name)}\s*=(?!=)", rows[k]["code"]):
                        limit = k - 1  # 指针被改写,保护到此为止
                        break
                spans.append((body_hi + 1, limit))
            guarded.setdefault(name, []).extend(spans)

        for name, spans in guarded.items():
            for idx in range(fstart, fend + 1):
                code = rows[idx]["code"]
                if not re.search(rf"\b{re.escape(name)}\s*->", code):
                    continue
                if any(s <= idx <= e for s, e in spans):
                    continue
                if RE_NULL_CHECK.search(code) or RE_NOT_CHECK.search(code):
                    continue
                out.append(Finding(
                    "BackwardNull", path, idx + 1,
                    f"指针 {name} 在行 {spans[0][0] + 1} 判空后被解引用,"
                    f"且解引用点不在任一判空分支内,需人工确认是否可能为空",
                ))
    return out


def _exit_after_condition(code: str) -> bool:
    """单行 if:条件右括号之后(可再跳过一层 '{')是否是退出语句。

    `if (p == nullptr) return;`、`if (!p) { return 0; }`、
    `if (p == nullptr || p->next == nullptr) return 0;` 三种都算。
    判据必须是"条件之后的那条语句",不能是"整行是否以 return 开头"——
    后者对以 if 开头的行永远不匹配,单行 guard clause 便全部漏保护。
    """
    depth = 0
    for pos, ch in enumerate(code):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                rest = code[pos + 1:].lstrip()
                if rest.startswith("{"):
                    rest = rest[1:].lstrip()
                return bool(RE_EXIT_STMT.match(rest))
    return False


def _guard_exits(rows: list[dict], check_idx: int, brace_idx: int | None,
                 body_lo: int, body_hi: int) -> bool:
    """判空分支是否以退出语句结束(提前返回惯用法)。

    多行形态看函数体最后一条语句,单行形态看条件右括号之后的那条语句。
    只认前者会把最常见的单行 guard clause 全报成候选——而
    standards-registry / SKILL.md / README 三处都承诺"已排除该惯用法"。
    """
    if brace_idx is None:
        return _exit_after_condition(rows[check_idx]["code"])
    return bool(RE_EXIT_STMT.match(_last_code_line(rows, body_lo, body_hi)))


def _find_open_brace(rows: list[dict], from_idx: int, limit: int) -> int | None:
    """从 from_idx 起找 if 体的 '{' 所在行;单行 if 或跨行太多则返回 None。"""
    for k in range(from_idx, min(from_idx + 4, limit + 1)):
        if rows[k]["code"].rstrip().endswith("{"):
            return k
    return None


def _last_code_line(rows: list[dict], lo: int, hi: int) -> str:
    """区间内最后一条语句(跳过空行、注释行,以及单独的右花括号行)。"""
    for k in range(hi, lo - 1, -1):
        stripped = rows[k]["code"].strip()
        if stripped and not rows[k]["is_comment"] and stripped != "}":
            return stripped
    return ""




def collect(paths: list[str], max_line: int = 120, max_func: int = 80) -> list[Finding]:
    """返回全部违规。路径下没有 C/C++ 源文件时抛 NoSourceFiles。"""
    files = collect_files(paths, CPP_SUFFIXES)
    if not files:
        raise NoSourceFiles("没有找到 C/C++ 源文件")
    findings: list[Finding] = []
    for path in files:
        findings.extend(detect_file(path, max_line, max_func))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = build_parser("C/C++ 规范检测")
    args = parser.parse_args(argv)

    label = os.path.basename(__file__)[:-3]
    return finish(
        lambda: collect(args.paths, args.max_line, args.max_func),
        label, args.json)


if __name__ == "__main__":
    sys.exit(main())
