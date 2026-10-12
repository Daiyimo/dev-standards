#!/usr/bin/env python3
"""detect_go 的回归测试。

守两条曾经静默失效的路径:

1. **分组状态机泄漏**(P0)。函数内的局部 `const (` 同样匹配 RE_GROUP_OPEN,
   而它的收尾 ')' 在 depth_before >= 1 的行上、上面那道 depth 门槛永不
   成立,group_kind 会一直泄漏到文件末尾——该行之后所有顶层声明检查都被
   continue 掉,而退出码还是正常的 0/1。一个局部常量分组就让文件后半段
   不再被检查,正是 _common 里"最危险的静默通过"。
2. **局部变量被当成导出符号**(P1)。var/const 那条分支用 stripped 而非
   code,函数体内的局部变量于是匹配上"行首 var",被报成 golint/varcomment
   ——golint 从不要求局部变量写文档注释。

跑法: python -m pytest scripts/test_detect_go.py -q
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# pylint: disable=wrong-import-position
import detect_go  # noqa: E402
from _testhelpers import lines_of, rules_of, write_source  # noqa: E402


# 一个函数里写了局部 const 分组;它后面还有一个导出函数。
LOCAL_GROUP = """\
package sample

func WithLocalGroup() int {
\tconst (
\t\tLocalConst = 1
\t)
\treturn LocalConst
}

func AfterTheGroup() int {
\treturn 2
}
"""

# 与上面只差"没有局部 const 分组"这一处,用来证明差异确实来自分组。
NO_LOCAL_GROUP = """\
package sample

func WithLocalGroup() int {
\treturn 1
}

func AfterTheGroup() int {
\treturn 2
}
"""

# 函数体内的局部 var/const,首字母大写也不是导出符号。
LOCAL_VARS = """\
package sample

func Undocumented() int {
\tvar LocalExported = 1
\tconst LocalConst = 2
\treturn LocalExported + LocalConst
}
"""

# 顶层导出变量:无注释的要报,有注释的不报——防止"干脆一条都不报"。
TOP_LEVEL_VARS = """\
package sample

// Documented has a comment.
var Documented = 1

var Undocumented = 2
"""

# 带 BOM 且包注释齐全的文件。
BOM_SAMPLE = """\
// Package sample does things.
package sample

func Undocumented() int {
\treturn 1
}
"""


def test_local_const_group_does_not_hide_later_declarations(tmp_path):
    """函数内的局部 const ( 不能关掉文件后半段的顶层声明检查。"""
    path = write_source(tmp_path, "local_group.go", LOCAL_GROUP)

    findings = detect_go.collect([path])

    # 第 3 行与第 10 行的两个导出函数都必须报;漏掉第 10 行就是状态机泄漏
    assert lines_of(findings, "golint/funccomment") == [3, 10]


def test_local_const_group_is_the_only_difference(tmp_path):
    """对照用例:去掉局部 const 分组,结果必须和上面完全一致。

    没有这条,"修好状态机泄漏"可能只是整体不再报任何东西。
    """
    path = write_source(tmp_path, "no_local_group.go", NO_LOCAL_GROUP)

    findings = detect_go.collect([path])

    # 对照组没有 const 块,第二个函数在第 7 行;与上一条只差这一处
    assert lines_of(findings, "golint/funccomment") == [3, 7]


def test_local_var_is_not_reported_as_exported_symbol(tmp_path):
    """函数体内的局部 var/const 不是导出符号,不该报 varcomment。"""
    path = write_source(tmp_path, "local_vars.go", LOCAL_VARS)

    findings = detect_go.collect([path])

    assert "golint/varcomment" not in rules_of(findings)


def test_top_level_exported_var_still_reported(tmp_path):
    """对照用例:顶层无注释的导出变量仍然要报。"""
    path = write_source(tmp_path, "top_vars.go", TOP_LEVEL_VARS)

    findings = detect_go.collect([path])

    assert lines_of(findings, "golint/varcomment") == [6]


def test_bom_file_package_comment_detected(tmp_path):
    """带 BOM 的 Go 文件:包注释要能认出来,不能因行首 \\ufeff 漏检。"""
    path = write_source(tmp_path, "bom.go", BOM_SAMPLE, encoding="utf-8-sig")

    findings = detect_go.collect([path])

    assert "golint/package" not in rules_of(findings)
    assert lines_of(findings, "golint/funccomment") == [4]


def test_collect_raises_when_no_go_files(tmp_path):
    """没有 .go 文件时必须抛 NoSourceFiles——空列表和"无违规"无法区分。"""
    (tmp_path / "notes.txt").write_text("not go", encoding="utf-8")

    with pytest.raises(detect_go.NoSourceFiles):
        detect_go.collect([str(tmp_path)])


TOP_LEVEL_INTERFACE = """\
package sample

type TopIface interface {
    Do()
}

type TopStruct struct {
    A int
}
"""


def test_top_level_interface_uses_interfacecomment(tmp_path):
    """顶层 type 也分 interface 与 struct,规则名不能串。

    曾在这里硬编码 structcomment,于是顶层接口全被报成 structcomment,而
    `--rules golint/interfacecomment` 一条都筛不到——registry 把两条当成独立
    规则列了两行,门禁按规则名筛时静默少一批。分组内的判据本来是对的,顶层漏了。
    """
    path = write_source(tmp_path, "iface.go", TOP_LEVEL_INTERFACE)

    findings = detect_go.collect([path])

    assert rules_of(findings) >= {"golint/interfacecomment", "golint/structcomment"}
    iface = [f for f in findings if f.rule == "golint/interfacecomment"]
    assert [f.line for f in iface] == [3]
    assert "TopIface" in iface[0].message


def test_main_returns_2_when_no_go_files(tmp_path):
    """退出码 2 的契约:没查到任何文件不等于无违规。"""
    (tmp_path / "notes.txt").write_text("not go", encoding="utf-8")

    assert detect_go.main([str(tmp_path)]) == 2


# 每个包只需要一处包注释,写在任意一个文件里都算。
PACKAGE_MISSING = """\
package sample

func Do() {}
"""


def test_missing_package_comment_is_reported(tmp_path):
    """缺包注释要报 golint/package。

    变异测试实测:把这条规则整段关掉,56 条测试照样全绿——`test_bom_file_...`
    只断言"带 BOM 时不漏报",没有任何正样本说"没有包注释时必须报"。
    """
    path = write_source(tmp_path, "nopkg.go", PACKAGE_MISSING)

    findings = detect_go.collect([path])

    assert "golint/package" in rules_of(findings)


def test_go_fnsize_rule_can_be_reported(tmp_path):
    """Go 的 golint/fnsize 本身要能报出来,行号还得对。

    变异测试实测两件事都没人守:整条规则关掉照样全绿;行号 idx+1 改成 idx 也全绿。
    """
    body = "\n".join(f"    x{i} := {i}" for i in range(20))
    path = write_source(tmp_path, "big.go",
                        "package sample\n\nfunc Big() {\n" + body + "\n}\n")

    findings = detect_go.collect([path], max_func=10)

    assert "golint/fnsize" in rules_of(findings)
    assert lines_of(findings, "golint/fnsize") == [3]


GO_TYPE_GROUP = """\
package sample

type (
    UndocIface interface {
        Do()
    }
    UndocStruct struct {
        A int
    }
)
"""


def test_type_group_specs_are_classified(tmp_path):
    """`type ( ... )` 分组块里的接口和结构体要走对规则名。

    变异测试实测:把 RE_GROUP_OPEN 的 type 分支关掉,`_group_spec_finding` 的
    group_kind=="type" 那一支整个没有测试覆盖——现在它能正确报,删了没人知道。
    """
    path = write_source(tmp_path, "group.go", GO_TYPE_GROUP)

    findings = detect_go.collect([path])

    assert rules_of(findings) >= {"golint/interfacecomment",
                                  "golint/structcomment"}
    # 分组里的接口和结构体都该按自己的规则名报,且行号各就各位
    assert lines_of(findings, "golint/interfacecomment") == [4]
    assert lines_of(findings, "golint/structcomment") == [7]


def test_group_close_depth_guard_keeps_fields_out(tmp_path):
    """分组关闭侧的 depth 门槛是承重的:去掉它,结构体字段会被误报成导出符号。

    变异测试实测:把 `depth_before == 0` 这个门槛从关闭侧去掉,
    `type ( S struct { Upper int } )` 里的字段 Upper 会被报成 golint/structcomment。
    """
    src = "package sample\n\ntype (\n    S struct {\n        Upper int\n    }\n)\n"
    path = write_source(tmp_path, "fields.go", src)

    findings = detect_go.collect([path])

    assert "Upper" not in " ".join(f.message for f in findings)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
