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


def test_main_returns_2_when_no_go_files(tmp_path):
    """退出码 2 的契约:没查到任何文件不等于无违规。"""
    (tmp_path / "notes.txt").write_text("not go", encoding="utf-8")

    assert detect_go.main([str(tmp_path)]) == 2


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
