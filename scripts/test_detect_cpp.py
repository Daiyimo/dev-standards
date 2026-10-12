#!/usr/bin/env python3
"""detect_cpp 的回归测试。

守三条曾经失效的路径:

1. **花括号不配对时整个扫描崩掉**(P0)。`#if 0` 条件编译里的 `{` 让 depth
   永远回不到基线,`while close <= fend` 于是把 close 推到 fend+1,紧接着
   `range(body_hi + 1, fend + 1)` 越界;`function_defs` 的 end 也会一路走到
   len(rows)。两者抛的异常都不在 finish() 的契约里(它只接 NoSourceFiles),
   于是一路穿透 collect → detect_file → main:零条明细、退出码 1。
2. **预处理指令被切成伪函数**。同一个样本里 `#if 0` 下的 `if (x) {` 会被
   回溯签名到 `#if 0` 那一行,从 "#if 0 if (x)" 里切出函数名 `if`——
   一个叫 if 的函数,区间还跨越大半个文件。
3. **单行 guard clause 全部误报**(P1)。"判空后提前返回"的豁免判据用的是
   "整行是否以 return 开头",对以 if 开头的行永远不匹配。而
   standards-registry / SKILL.md / README 三处都承诺已排除该惯用法。

跑法: python -m pytest scripts/test_detect_cpp.py -q
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# pylint: disable=wrong-import-position
import detect_cpp  # noqa: E402
from _testhelpers import lines_of, rules_of, write_source  # noqa: E402
from _common import scan_lines  # noqa: E402


# 花括号不配对:#if 0 里的 '{' 让 depth 永远回不到基线。
# 末尾那条超长行是正向断言用——证明扫描真的跑完了,不是悄悄什么都没做。
PREPROCESSOR = """\
void f(int* p) {
#if 0
    if (disabled) {
#endif
    if (p == nullptr)
    {
        return;
    }
    return p->x;
    // %s
}
""" % ("x" * 130)

# 用 %s 占位而不是 .format():模板里全是 C++ 花括号,.format 会把它们当成
# 字段定界符,每个 guard 都得写成 {{ }} 才能用。
GUARD_TEMPLATE = """\
void f(Node* p) {
    %s
    return p->x;
}
"""

# 提前返回惯用法的三种常见单行写法,一个都不能报。
GUARD_FORMS = [
    "if (p == nullptr) return;",
    "if (!p) { return 0; }",
    "if (p == nullptr || p->next == nullptr) return 0;",
]

# 判空分支不以退出语句结尾:p 确实可能在解引用点为空,必须报。
REAL_CANDIDATE = """\
void g(Node* p) {
    if (p == nullptr) {
        log();
    }
    return p->x;
}
"""

# 多行判空条件:续行受 || / && 短路保护,不是候选。
MULTILINE_CONDITION = """\
void f(Node* p) {
    if (p == nullptr ||
        p->next == nullptr)
    {
        return 0;
    }
    return p->x;
}
"""


def test_unbalanced_braces_do_not_crash(tmp_path):
    """花括号不配对不能让扫描崩掉,也不能让结果凭空消失。"""
    path = write_source(tmp_path, "pp.cpp", PREPROCESSOR)

    findings = detect_cpp.collect([path])

    # 超长行仍在,说明扫描完整跑完了而不是早早返回
    assert lines_of(findings, "tosa/linelength") == [10]


def test_preprocessor_directive_is_not_part_of_a_signature(tmp_path):
    """#if 0 里的 if (x) { 不能被切成一个叫 if 的函数。"""
    path = write_source(tmp_path, "pp.cpp", PREPROCESSOR)
    with open(path, "r", encoding="utf-8") as handle:
        rows = scan_lines(handle.read(), raw_strings=False)

    names = [name for name, _start, _end in detect_cpp.function_defs(rows)]

    assert "if" not in names
    assert "f" in names


@pytest.mark.parametrize("guard", GUARD_FORMS)
def test_single_line_guard_clause_is_not_reported(tmp_path, guard):
    """提前返回惯用法的三种单行写法都不能报 BackwardNull。"""
    path = write_source(tmp_path, "guard.cpp", GUARD_TEMPLATE % guard)

    findings = detect_cpp.collect([path])

    assert "BackwardNull" not in rules_of(findings)


def test_multiline_condition_continuation_is_not_reported(tmp_path):
    """多行判空条件的续行受短路保护,不能报成候选。"""
    path = write_source(tmp_path, "multiline.cpp", MULTILINE_CONDITION)

    findings = detect_cpp.collect([path])

    assert "BackwardNull" not in rules_of(findings)


def test_guard_and_real_candidate_are_distinguished(tmp_path):
    """同一个文件里:惯用法不报,真候选要报。

    没有这条对照,"不再误报"可能只是整体把 BackwardNull 关掉了。
    """
    body = (GUARD_TEMPLATE % GUARD_FORMS[0] + "\n"
            + REAL_CANDIDATE)
    path = write_source(tmp_path, "mixed.cpp", body)

    findings = detect_cpp.collect([path])

    # 只有第二个函数里那条解引用(第 10 行)是候选
    assert lines_of(findings, "BackwardNull") == [10]


def test_collect_raises_when_no_cpp_files(tmp_path):
    """没有 C/C++ 源文件时必须抛 NoSourceFiles。"""
    (tmp_path / "notes.txt").write_text("not cpp", encoding="utf-8")

    with pytest.raises(detect_cpp.NoSourceFiles):
        detect_cpp.collect([str(tmp_path)])


def test_main_returns_2_when_no_cpp_files(tmp_path):
    """退出码 2 的契约:没查到任何文件不等于无违规。"""
    (tmp_path / "notes.txt").write_text("not cpp", encoding="utf-8")

    assert detect_cpp.main([str(tmp_path)]) == 2


# 尾部修饰符任意多个并列:const noexcept / const override / noexcept override 都是
# 常见成员函数写法。曾因正则只允许一个关键字,这些函数整条判不出是函数。
MULTI_SUFFIX = """\
void Big::step(int a) const noexcept
{
    int x0 = 0;
    int x1 = 1;
    int x2 = 2;
    return;
}

void Small::tick() const override
{
    return;
}
"""


def test_multiple_trailing_qualifiers_are_still_functions(tmp_path):
    """const noexcept / const override 必须认得出是函数。

    曾写成 `(?:const|noexcept|override|final)?`——只允**一个**关键字,于是
    `void f() const noexcept` 整条判不出是函数,function_defs 直接返回 [],
    golint/fnsize 和 BackwardNull 双双漏检。这是极常见的成员函数写法,
    影响面比已记录的 raw string 更大。
    """
    path = write_source(tmp_path, "multi.cpp", MULTI_SUFFIX)

    findings = detect_cpp.collect([path], max_func=6)

    names = {f.message.split(" ")[1] for f in findings if f.rule == "golint/fnsize"}
    assert "step" in names, findings
    assert "tick" not in names          # 3 行,不超 max_func=6


def test_cpp_fnsize_rule_can_be_reported(tmp_path):
    """C++ 的 golint/fnsize 本身要能报出来。

    变异测试实测:把 detect_cpp 里的 fnsize 分支整段关掉,56 条测试照样全绿——
    这条规则处于"删了没人发现"状态。这条用例补上那个洞。
    """
    body = "\n".join(f"    x{i} = {i};" for i in range(20))
    path = write_source(tmp_path, "big.cpp",
                        "int big() {\n" + body + "\n    return 0;\n}\n")

    findings = detect_cpp.collect([path], max_func=10)

    assert "golint/fnsize" in rules_of(findings)
    assert lines_of(findings, "golint/fnsize") == [1]


def test_close_guard_prevents_index_error(tmp_path):
    """`close < fend` 这个夹紧是承重的:去掉它就崩。

    样本让**函数自己的**右花括号也被行注释吃掉,于是深度永远回不到基线,close 会
    一路走到 fend+1,body_hi 随之越界。变异测试实测把 `close < fend` 改成
    `close <= fend` 立刻 IndexError——正是源码注释里说要防的那个崩溃,
    而它此前没有测试守(只用"右花括号被注释吃掉"的样本复现不出来,
    那时函数末尾的 `}` 会把 depth 拉回基线,close 根本不会走过头)。
    """
    src = ("void f(Node* p) {\n"
           "    if (p == nullptr) {\n"
           "        return;\n"
           "    return p->x;\n"
           "// }\n")
    path = write_source(tmp_path, "unclosed.cpp", src)

    findings = detect_cpp.collect([path], max_func=1)

    assert isinstance(findings, list)      # 不抛异常即通过


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
