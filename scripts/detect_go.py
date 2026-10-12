#!/usr/bin/env python3
"""detect_go 报告 Go 代码中违反静态扫描规范的位置。

用法:
    python detect_go.py <目录或文件...> [--max-line N] [--max-func N] [--json]

输出每行一条: <规范规则名>\\t<文件>:<行>\\t<说明>
退出码: 0 = 无违规, 1 = 有违规, 2 = 自身错误(含路径下没有 .go 文件)

本检测器是**轻量行扫描器,不是完整的 Go 解析器**。它假设代码经过 gofmt
(声明与右花括号都在行首),这对正常项目成立。已知边界:

  - 不理解 struct tag、cgo、build tag 里的花括号;
  - 多行函数签名按"首行到配套右花括号"计行数,与人工数法一致;
  - 导出标识符靠行首正则识别,写在字符串或注释里的同名不算;
  - 源文件按 UTF-8 → GBK → latin-1 顺序探测编码,见 _common.read_source。

项目里若有 golangci-lint / revive,以它们为准更权威;本工具的价值是
**不依赖 Go 工具链也能跑**,且在没装那些工具的环境里给出同一套规则名。

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

# 顶层声明(必须在行首,gofmt 保证)
RE_FUNC = re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)")
RE_TYPE = re.compile(r"^type\s+([A-Za-z_]\w*)")
RE_GROUP_OPEN = re.compile(r"^(?:var|const|type)\s*\(\s*$")
RE_GROUP_SPEC = re.compile(r"^\s*([A-Za-z_]\w*)")
RE_PACKAGE = re.compile(r"^package\s+([A-Za-z_]\w*)")
# 类型种类关键字。必须带词边界:单行写法 `type S struct { interfaceX int }` 里
# 字段名含 interface,用子串判据会把结构体误判成接口(实测确实误报)。
RE_TYPE_KIND = re.compile(r"\b(interface|struct)\b")


def _is_interface_spec(text: str) -> bool:
    """这行声明的是接口还是结构体。

    取**第一个** interface / struct 关键字:名字之后那个才是类型种类,字段名、
    内联类型里的同名单词不算。词边界保证 `interfaceX` 不匹配。
    """
    match = RE_TYPE_KIND.search(text)
    return bool(match) and match.group(1) == "interface"



# ---------------------------------------------------------------------------
GO_SUFFIXES = (".go",)


def _contiguous_comment_above(rows: list[dict], idx: int) -> list[str]:
    """取紧贴 rows[idx] 上方、连续的整行注释原文。"""
    out: list[str] = []
    j = idx - 1
    while j >= 0 and rows[j]["is_comment"]:
        out.append(rows[j]["raw"].strip())
        j -= 1
    return out


def _has_doc_comment(rows: list[dict], idx: int) -> bool:
    """行首紧贴的连续 // 注释块即文档注释(gofmt 下与声明之间无空行)。"""
    return bool(_contiguous_comment_above(rows, idx))


def _package_comment_findings(rows: list[dict], path: str) -> list[Finding]:
    """包注释:每个包只需一处 `// Package xxx ...`。"""
    for idx, row in enumerate(rows):
        match = RE_PACKAGE.match(row["code"].strip())
        if not match:
            continue
        pkg = match.group(1)
        comments = _contiguous_comment_above(rows, idx)
        if any(c.startswith(f"// Package {pkg}") for c in comments):
            return []
        return [Finding(
            "golint/package", path, idx + 1,
            f"package {pkg} 需要包注释(// Package {pkg} ...)",
        )]
    return []


def _doc_comment_finding(rule: str, kind: str, name: str,
                         rows: list[dict], idx: int, path: str) -> Finding | None:
    """导出符号缺注释时返回一条 Finding,否则 None。"""
    if not name[:1].isupper() or _has_doc_comment(rows, idx):
        return None
    return Finding(rule, path, idx + 1, f"导出{kind} {name} 需要有注释说明")


def _group_spec_finding(group_kind: str, code: str, stripped: str,
                        rows: list[dict], idx: int, path: str) -> Finding | None:
    """分组块(var/const/type 的圆括号块)内单个 spec 的注释检查。"""
    match = RE_GROUP_SPEC.match(code)
    if not match:
        return None
    name = match.group(1)
    if group_kind == "type":
        # 接口与结构体分开报,和顶层声明保持同一套规则名。
        is_iface = _is_interface_spec(stripped)
        rule = "golint/interfacecomment" if is_iface else "golint/structcomment"
        kind = "接口" if is_iface else "结构体"
    else:
        rule, kind = "golint/varcomment", "变量/常量"
    return _doc_comment_finding(rule, kind, name, rows, idx, path)


def _func_size_finding(rows: list[dict], idx: int, name: str, path: str,
                       max_func: int) -> Finding | None:
    """函数规模:签名首行到右花括号回到深度 0 的那一行。

    单独一个函数,不内联进 _declaration_findings:那段把后者的圈复杂度顶到了
    阈值(实测 20),而"这个函数几行"本来就是一个独立问题。2.1.0 拆
    detect_file 时也是同样理由。
    """
    end = idx
    while end < len(rows) and rows[end]["depth_after"] != 0:
        end += 1
    size = (end - idx) + 1
    if size > max_func:
        return Finding(
            "golint/fnsize", path, idx + 1,
            f"函数 {name} 的行数是 {size},规范设定值为 {max_func}",
        )
    return None


def _declaration_findings(rows: list[dict], path: str, max_func: int) -> list[Finding]:
    """顶层与分组内的导出符号注释,以及函数规模。"""
    out: list[Finding] = []
    group_kind: str | None = None   # 当前所处的 var/const/type 分组块

    for idx, row in enumerate(rows):
        code = row["code"]
        stripped = code.strip()

        # 分组块内部。注意:var/const/type 用圆括号分组,不增加花括号深度,
        # 所以分组内的 spec 仍在 depth_before == 0。
        if group_kind:
            if row["depth_before"] == 0:
                if stripped.startswith(")"):
                    group_kind = None
                elif stripped:
                    finding = _group_spec_finding(
                        group_kind, code, stripped, rows, idx, path)
                    if finding:
                        out.append(finding)
            continue

        # 只在顶层开组。函数内的局部 `const (` / `var (` 同样匹配 RE_GROUP_OPEN,
        # 但它的收尾 ')' 在 depth_before >= 1 的行上,上面第 119 行的条件永不
        # 成立,group_kind 会一直泄漏到文件末尾——之后的 func / type / 函数规模
        # 检查全部被 continue 掉,而退出码还是正常的 0/1。这正是 _common 里
        # "最危险的静默通过":一个局部常量分组就让整个文件后半段不再被检查。
        if row["depth_before"] == 0:
            group_open = RE_GROUP_OPEN.match(stripped)
            if group_open:
                group_kind = group_open.group(0).split()[0]
                continue

        func_match = RE_FUNC.match(code)
        if func_match:
            name = func_match.group(1)
            finding = _doc_comment_finding(
                "golint/funccomment", "函数", name, rows, idx, path)
            if finding:
                out.append(finding)
            size_finding = _func_size_finding(rows, idx, name, path, max_func)
            if size_finding:
                out.append(size_finding)
            continue

        type_match = RE_TYPE.match(code)
        if type_match:
            # 和 _group_spec_finding 用同一套判据:顶层 type 也分 interface 与
            # struct。曾在这里硬编码 structcomment,于是顶层接口全被报成
            # structcomment,而 `--rules golint/interfacecomment` 一条都筛不到——
            # registry 把两条当成独立规则列了两行,门禁按规则名筛时静默少一批。
            is_iface = _is_interface_spec(stripped)
            finding = _doc_comment_finding(
                "golint/interfacecomment" if is_iface else "golint/structcomment",
                "接口" if is_iface else "类型",
                type_match.group(1), rows, idx, path)
            if finding:
                out.append(finding)
            continue

        # 用 code 而不是 stripped:code 保留缩进,天然排除函数体内的局部
        # var/const(golint 从不要求局部变量写文档注释)。与上面 RE_FUNC /
        # RE_TYPE 保持同一套口径。
        var_match = re.match(r"^(?:var|const)\s+([A-Za-z_]\w*)", code)
        if var_match:
            finding = _doc_comment_finding(
                "golint/varcomment", "变量/常量", var_match.group(1), rows, idx, path)
            if finding:
                out.append(finding)

    return out


def detect_file(path: str, max_line: int, max_func: int) -> list[Finding]:
    try:
        text = read_source(path)
    except OSError as exc:
        print(f"detect_go: 跳过 {path}: {exc}", file=sys.stderr)
        # 读不了的文件不能悄悄不计入:否则退出码仍是 0,门禁把"没查"当成
        # "查过了没问题"——和 NoSourceFiles 要防的是同一类静默通过。
        return [Finding("error", path, 0, f"无法读取: {exc}")]

    rows = scan_lines(text)
    out: list[Finding] = overlong_lines(rows, path, max_line)
    out.extend(_package_comment_findings(rows, path))
    out.extend(_declaration_findings(rows, path, max_func))
    return out


def collect(paths: list[str], max_line: int = 120, max_func: int = 80) -> list[Finding]:
    """返回全部违规。路径下没有 .go 文件时抛 NoSourceFiles。"""
    files = collect_files(paths, GO_SUFFIXES)
    if not files:
        raise NoSourceFiles("没有找到 .go 文件")
    findings: list[Finding] = []
    for path in files:
        findings.extend(detect_file(path, max_line, max_func))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = build_parser("Go 规范检测")
    args = parser.parse_args(argv)

    label = os.path.basename(__file__)[:-3]
    return finish(
        lambda: collect(args.paths, args.max_line, args.max_func),
        label, args.json)


if __name__ == "__main__":
    sys.exit(main())
