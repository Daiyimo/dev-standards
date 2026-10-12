#!/usr/bin/env python3
"""_common 的回归测试。

守两条被三个检测器共用的地基:

1. **read_source 的编码探测**。原来用 `encoding="utf-8", errors="replace"`
   硬解,GBK 编码的中文每个字 2 字节都变成 U+FFFD,行宽按字符数一算就翻倍
   (实测一条真实 94 字符的 C++ 行被判成 154 字符并报 line-too-long)。
   `utf-8-sig` 顺带修掉带 BOM 的 Go 文件包注释漏检。
2. **scan_lines 的行语义**。Go 与 C++ 检测器的函数区间、分组状态、
   BackwardNull 全部由 depth_before / depth_after / code 派生,而它此前
   一条测试都没有——连"字符串里的花括号不算深度"都没人守过。

跑法: python -m pytest scripts/test_detect_common.py -q
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# pylint: disable=wrong-import-position
import _common  # noqa: E402
from _testhelpers import write_bytes, write_source  # noqa: E402


def _depths(text: str, **kwargs) -> list[int]:
    return [row["depth_after"] for row in _common.scan_lines(text, **kwargs)]


# ---------------------------------------------------------------------------
# read_source
# ---------------------------------------------------------------------------

def test_read_source_plain_utf8(tmp_path):
    """UTF-8 无 BOM:原样解出,中文一个字符就是一个字符。"""
    body = "// 中文注释\nx = 1\n"
    path = write_source(tmp_path, "plain.py", body)

    assert _common.read_source(path) == body


def test_read_source_strips_bom(tmp_path):
    """带 BOM 的文件要吃掉 BOM。

    吃不掉时行首多一个 \\ufeff,它不是 Python 的空白字符、strip() 掉不了,
    于是该行既不被判成注释行,`RE_PACKAGE` 也匹配不上——带 BOM 的 Go 文件
    包注释因此漏检。
    """
    body = "// Package sample does things.\npackage sample\n"
    path = write_source(tmp_path, "bom.go", body, encoding="utf-8-sig")

    assert _common.read_source(path) == body


def test_read_source_gbk_keeps_character_count(tmp_path):
    """GBK 文件按 GBK 解,字符数和编辑器里看到的一致。

    这是行宽"中文不按字节误判"的另一半:按字节计的 awk 会多出假阳性,
    按 UTF-8 + errors="replace" 硬解同样会——GBK 中文每字 2 字节都变成
    U+FFFD,长度按 1.6 倍左右膨胀。断言解码后的长度等于写入时的长度,
    不依赖 120 阈值,因此不会因为阈值调整而失效。
    """
    text = "// " + "注释" * 35          # 72 个字符
    path = write_source(tmp_path, "gbk.cpp", text, encoding="gbk")

    decoded = _common.read_source(path)

    assert len(decoded.rstrip("\n")) == len(text)


def test_read_source_latin1_fallback_never_fails(tmp_path):
    """既非 UTF-8 也非 GBK 时按 latin-1 兜底,永不抛异常。"""
    path = write_bytes(tmp_path, "weird.bin", b"\xff\xfe\x00\x81")

    assert isinstance(_common.read_source(path), str)


# ---------------------------------------------------------------------------
# scan_lines
# ---------------------------------------------------------------------------

def test_scan_lines_ignores_braces_inside_strings():
    """字符串里的花括号不参与深度计数。

    这条错了,Go/C++ 的函数区间、分组状态、BackwardNull 全线失真——
    而它们全部由 depth_before / depth_after 派生。
    """
    text = 'var s = "{"\nfunc f() {\n}\n'

    assert _depths(text) == [0, 1, 0]


def test_scan_lines_separates_raw_and_code():
    """raw 保留原文(行宽按它算),code 去掉注释与字符串内容。"""
    rows = _common.scan_lines("int a; // { not a brace\n")

    assert rows[0]["raw"] == "int a; // { not a brace"
    assert rows[0]["code"] == "int a; "
    assert rows[0]["depth_after"] == 0
    assert rows[0]["is_comment"] is False


def test_scan_lines_marks_full_line_comments():
    """整行注释的 code 为空、is_comment 为真——文档注释判定靠这个。"""
    rows = _common.scan_lines("// Package sample\n")

    assert rows[0]["code"] == ""
    assert rows[0]["is_comment"] is True


def test_scan_lines_go_raw_string_spans_lines():
    """Go 反引号裸字符串跨行延续,里面的 func 与花括号都不算。

    detect_go 的 docstring 承诺"写在字符串或注释里的同名不算",靠的就是
    这条状态延续;raw_strings=False(C/C++)时反引号不是字符串定界符。
    """
    text = "var s = `\nfunc NotAFunc() {\n}\n`\n"

    go_rows = _common.scan_lines(text, raw_strings=True)
    cpp_rows = _common.scan_lines(text, raw_strings=False)

    assert all(row["code"].strip() == "" for row in go_rows[1:3])
    # 裸串里的 { 与 } 都不进深度;raw_strings=False 时反引号不是定界符,
    # 同一个文本在 C++ 侧会把 func 那一行的 { 算进去——两种口径都要钉住。
    assert _depths(text, raw_strings=True) == [0, 0, 0, 0]
    assert _depths(text, raw_strings=False) == [0, 1, 0, 0]
    assert cpp_rows[1]["code"].strip().startswith("func NotAFunc")


def test_scan_lines_block_comment_spans_lines():
    """跨行块注释按状态延续,/* 之后的花括号同样不算。"""
    text = "/*\n{\n*/\nvoid f() {\n}\n"

    assert _depths(text, raw_strings=False) == [0, 0, 0, 1, 0]


def test_split_lines_only_breaks_on_cr_lf():
    """split_lines 只认 \n / \r / \r\n 为行尾。

    曾用 str.splitlines():它还会在 0x0B / 0x0C / 0x1C / U+2028 处断行,于是一条
    真实在第 3 行的 133 字符行被报到第 4 行,而 pylint 报第 3 行——整份报告的行号
    全部偏移,修告警的人会改到错的代码上。口径照抄 pylint 的 specific_splitlines。
    """
    text = "a\n\x0cb\nc\nd"

    assert _common.split_lines(text) == ["a", "\x0cb", "c", "d"]


def test_split_lines_keeps_trailing_line_count():
    """文本以换行结尾时不多出一个空行。

    这是行号的基础:多一个空元素,scan_lines 的行数就比编辑器多一行,Go 的 fnsize
    区间会直接扫到 EOF。用 re.split 实现时踩过这个坑。
    """
    assert _common.split_lines("a\nb\n") == ["a", "b"]
    assert _common.split_lines("a\nb") == ["a", "b"]


def test_split_lines_matches_pylint_specific_splitlines():
    """和 pylint 的实现逐字对齐——两条路径的行号必须一致。"""
    from pylint.checkers.format import FormatChecker

    for text in ("a\n\x0cb\nc\n", "a\rb\r\nc", "x\x0cy\x0cz",
                 "a\n\n\nb", "\u2028a\nb"):
        assert _common.split_lines(text) == [
            line.rstrip("\r\n")
            for line in FormatChecker.specific_splitlines(text)
        ], repr(text)


def test_scan_lines_row_count_not_inflated_by_form_feed():
    """0x0C 不能让 scan_lines 多出一行——行号是全报告的地基。

    上面几条直接测 split_lines,这条测**调用方真的用了它**。曾把 scan_lines 里的
    split_lines(text) 换回 text.splitlines(),上面几条照样全绿,而真实文件里一条
    第 3 行的告警被报到第 4 行——修告警的人会改到错的代码上。
    """
    text = "int a;\n\x0cint b;\nint c;\n"

    rows = _common.scan_lines(text)

    assert len(rows) == 3
    assert rows[2]["code"].strip() == "int c;"


def test_scan_lines_does_not_expose_indent_field():
    """indent 曾是死字段:每行都算,三个检测器没有一处读取。

    留着一个没人读的键,下一个人会以为它有用、据此写判断。删掉比留着便宜。
    """
    rows = _common.scan_lines("    int a;\n")

    assert "indent" not in rows[0]


def test_backslash_continued_string_does_not_count_braces():
    """行尾是反斜杠续行时,字符串状态必须带到下一行。

    `in_string` 曾是每行局部变量,于是行尾带反斜杠的 `"abc` 续行到
    `def } ghi";` 时,后者里的 `}` 被当成代码,深度从 1 掉到 0,整个函数的区间被
    截断——fnsize 和 BackwardNull 一起失真。实测修复前 function_defs 是
    [('f', 0, 2)],修复后是 [('f', 0, 4)]。
    """
    text = ('void f(Node* p) {\n'
            '    const char* s = "abc' + chr(92) + '\n'
            'def } ghi";\n'
            '    return p->x;\n'
            '}\n')

    rows = _common.scan_lines(text, raw_strings=False)

    assert [r["depth_after"] for r in rows] == [1, 1, 1, 1, 0]


def test_unterminated_quote_does_not_swallow_the_file():
    """落单的引号不能把后半文件吞掉——只有反斜杠续行才延续字符串状态。

    这是上一条的必要约束:C++14 的二进制位分隔符 `0b1010'1010` 里只有一个撇号,
    字符串状态从此永不闭合;若让它无条件跨行,从那之后的所有花括号都不再计数,
    整份报告的深度全错。

    样本必须用**奇数个**撇号:`1'000'000` 有两个,字符串会自己开合,测不出这条。
    """
    text = "int a = 0b1010'1010;\nint b() {\n    return 1;\n}\n"

    rows = _common.scan_lines(text, raw_strings=False)

    assert [r["depth_after"] for r in rows] == [0, 1, 1, 0]


def test_cpp_raw_string_braces_are_not_code():
    """C++11 raw string 里的花括号不是代码。

    `R"glsl( void main() { ... )glsl"` 里的 `{` 曾把深度推高,收尾的 `}` 又拉低,
    实测报出一个函数名叫 main 的 phantom 函数,真实函数区间被拽到 EOF。
    """
    text = ('void f(Node* p) {\n'
            '    auto shader = R"glsl(\n'
            'void main() {\n'
            '    gl_Position = vec4(1.0);\n'
            '}\n'
            ')glsl";\n'
            '    return p->x;\n'
            '}\n')

    rows = _common.scan_lines(text, raw_strings=False)

    assert [r["depth_after"] for r in rows] == [1, 1, 1, 1, 1, 1, 1, 0]


def test_cpp_raw_string_with_delimiter_and_nested_parens():
    """带 delim 的 raw string 也要认,且内容里的括号不影响深度。"""
    text = ('int g() {\n'
            '    auto s = R"xy(a ) b { c)xy";\n'
            '    return 1;\n'
            '}\n')

    rows = _common.scan_lines(text, raw_strings=False)

    assert [r["depth_after"] for r in rows] == [1, 1, 1, 0]


def test_go_backtick_raw_string_still_works():
    """对照:Go 的反引号裸串行为不能被上面几改动。"""
    text = "package p\n\nvar s = `\na { b\n`\n"

    rows = _common.scan_lines(text, raw_strings=True)

    assert [r["depth_after"] for r in rows] == [0, 0, 0, 0, 0]


def test_cpp_raw_string_not_enabled_for_go():
    """Go 模式下不启用 C++ raw string 语法(Go 里 R"( 没有特殊含义)。"""
    text = 'package p\n\nvar s = R"(a { b)"\n'

    rows = _common.scan_lines(text, raw_strings=True)

    # Go 侧把它当普通字符串处理,行为与修复前一致
    assert [r["depth_after"] for r in rows] == [0, 0, 0]


def test_skipped_dir_with_sources_is_reported(tmp_path, capsys):
    """跳过的目录里**有源文件**时必须说一声。

    没有这一行,"少扫一批文件而退出码不变"——门禁把没查当成查过了,正是
    NoSourceFiles 要防的那类静默通过,只是触发条件从路径打错换成目录名。
    """
    (tmp_path / "build").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "build" / "gen.py").write_text("import os\n", encoding="utf-8")
    (tmp_path / "src" / "a.py").write_text("import sys\n", encoding="utf-8")

    _common.collect_files([str(tmp_path)], (".py",))

    err = capsys.readouterr().err
    assert "build" in err
    assert "SKIP_DIRS" in err


def test_skipped_dir_without_sources_stays_quiet(tmp_path, capsys):
    """对照:跳过的目录里没有源文件时不要啰嗦。"""
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "out.o").write_text("binary", encoding="utf-8")
    (tmp_path / "a.py").write_text("import sys\n", encoding="utf-8")

    _common.collect_files([str(tmp_path)], (".py",))

    assert capsys.readouterr().err == ""


def test_empty_skip_dirs_means_skip_nothing(tmp_path):
    """skip_dirs=set() 表示什么都不跳过,不能静默回落成默认 SKIP_DIRS。

    `skip_dirs or SKIP_DIRS` 里空集合是 falsy,会被 or 吃成默认值。
    """
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "gen.py").write_text("import os\n", encoding="utf-8")

    found = _common.collect_files([str(tmp_path)], (".py",), skip_dirs=set())

    assert any(f.endswith("gen.py") for f in found)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
