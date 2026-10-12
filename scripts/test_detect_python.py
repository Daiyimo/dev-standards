#!/usr/bin/env python3
"""detect_python 的回归测试。

核心是 pylint 不可用时的回退路径:README 与 standards-registry 承诺
"装不上自动退回内置 AST 检测(只剩行宽 + CCN)"。这条路径曾静默漏报——
pylint_findings() 失败后返回空列表,但 use_pylint 没被翻成 False,
collect() 于是把 AST 的行宽结果整批过滤掉,回退后只剩 CCN。

跑法: python -m pytest scripts/test_detect_python.py -q
"""

from __future__ import annotations

import builtins
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# pylint: disable=wrong-import-position
import _common  # noqa: E402
from _common import Finding  # noqa: E402
import detect_python  # noqa: E402
from _testhelpers import lines_of  # noqa: E402

# 130 字符,超过 120 阈值;字符串字面量保持完整,只靠外层赋值超限。
# ("value = " 8 字符 + repr 的一对引号 2 + 120 个 x)
LONG_LINE = "value = " + repr("x" * 120)

# 圈复杂度 3(基础 1 + if + elif;elif 在 AST 里是嵌套 If)。return 不是判定点,
# 不加权——之前这里写 4 并把 return 算进算式,是错的。
BRANCHY = """\
def decide(flag):
    if flag:
        return 1
    elif flag is None:
        return 2
    return 3
"""

BROAD_EXCEPT = """\
try:
    pass
except Exception:
    pass
"""


def _pylint_unlaunchable(*args, **kwargs):
    """模拟进程起不来:解释器缺失、超时等,subprocess.run 抛异常。"""
    raise OSError("pylint not installed")


def _simulate_not_installed(monkeypatch):
    """模拟 pylint 未安装:可用性探测返回 None。

    同时断言实现不会真的去启动 pylint 进程——未安装时应当先探测、直接回退。
    少了这条断言,样本里的超长行会被真实 pylint 报出来,测试会因错误的原因通过。
    """
    def _must_not_spawn(*args, **kwargs):
        raise AssertionError("pylint 未安装时不应启动 pylint 进程")

    monkeypatch.setattr(detect_python.importlib.util, "find_spec",
                        lambda name: None)
    monkeypatch.setattr(detect_python.subprocess, "run", _must_not_spawn)


def _simulate_unlaunchable(monkeypatch):
    """模拟进程起不来:subprocess.run 抛 OSError。"""
    monkeypatch.setattr(detect_python.subprocess, "run", _pylint_unlaunchable)


def _pylint_installed() -> bool:
    """pylint 是否真的可用(用于跳过依赖它的用例)。"""
    try:
        proc = subprocess.run([sys.executable, "-m", "pylint", "--version"],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _write_sample(directory, body: str, name: str = "sample.py") -> str:
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)
    return path


def _rules(findings) -> set[str]:
    return {f.rule for f in findings}


# pylint 文本输出的一行。file 段带盘符时,老正则的 [^:]+ 会在 "C" 处断掉,
# 整行匹配失败——pylint 的全部结果被静默丢弃,检测器对满是违规的文件报绿灯。
_ABSOLUTE_LINE = (
    r"C:\build\agent\sample.py:3:7: W0718: "
    r"Catching too general exception Exception (broad-exception-caught)"
)
_RELATIVE_LINE = (
    r"pylintdbg\sample.py:3:7: W0718: "
    r"Catching too general exception Exception (broad-exception-caught)"
)


def test_parse_pylint_line_handles_relative_path():
    """常规形态:pylint 把路径相对化后的输出要能解析。"""
    finding = detect_python.parse_pylint_line(_RELATIVE_LINE)

    assert finding is not None
    assert finding.rule == "broad-except"
    assert finding.file == r"pylintdbg\sample.py"
    assert finding.line == 3


def test_parse_pylint_line_handles_windows_drive_letter():
    """跨盘扫描时 pylint 输出绝对路径,盘符里的冒号不能让它匹配失败。"""
    finding = detect_python.parse_pylint_line(_ABSOLUTE_LINE)

    assert finding is not None
    assert finding.rule == "broad-except"
    assert finding.file == r"C:\build\agent\sample.py"
    assert finding.line == 3


# pylint 不可用有两种真实形态,都必须回退到内置 AST:
#   未安装   —— 这个解释器 import 不到 pylint(find_spec 为 None)
#   起不来   —— 解释器缺失 / 超时,subprocess.run 抛 OSError
_UNAVAILABLE = pytest.mark.parametrize(
    "simulate_unavailable",
    [_simulate_not_installed, _simulate_unlaunchable],
    ids=["not-installed", "unlaunchable"],
)


@_UNAVAILABLE
def test_pylint_unavailable_still_reports_line_too_long(tmp_path, monkeypatch,
                                                        simulate_unavailable):
    """pylint 不可用时,行宽必须仍由内置 AST 检出,不能静默漏报。"""
    path = _write_sample(str(tmp_path), LONG_LINE + "\n")
    simulate_unavailable(monkeypatch)

    findings = detect_python.collect([path])

    assert "line-too-long" in _rules(findings)


@_UNAVAILABLE
def test_pylint_unavailable_still_reports_ccn(tmp_path, monkeypatch, simulate_unavailable):
    """回退路径的另一半:CCN 只有 AST 能算,同样不能丢。"""
    path = _write_sample(str(tmp_path), BRANCHY)
    simulate_unavailable(monkeypatch)

    findings = detect_python.collect([path], ccn_threshold=2)

    assert "ccn" in _rules(findings)


@_UNAVAILABLE
def test_pylint_unavailable_does_not_fabricate_pylint_only_rules(tmp_path,
                                                                 monkeypatch,
                                                                 simulate_unavailable):
    """broad-except 只有 pylint 能判;回退时不该凭 AST 假装报得出来。

    守住边界:修回退漏报的方式是让 AST 的行宽生效,不是把 pylint 专属规则
    也造一份假结果。
    """
    path = _write_sample(str(tmp_path), BROAD_EXCEPT)
    simulate_unavailable(monkeypatch)

    findings = detect_python.collect([path])

    assert "broad-except" not in _rules(findings)


def test_pylint_available_reports_pylint_only_rules(tmp_path):
    """对照用例:pylint 在时,它专属的规则确实能进来。

    同时验证上面三个用例的夹具没写错——否则"回退漏报"可能是夹具问题。
    """
    if not _pylint_installed():
        pytest.skip("pylint 未安装")
    path = _write_sample(str(tmp_path), BROAD_EXCEPT)

    findings = detect_python.collect([path])

    assert "broad-except" in _rules(findings)


def test_pylint_available_and_unavailable_agree_on_ast_rules(tmp_path, monkeypatch):
    """两种模式下 AST 可判的规则集合必须一致——这正是 README 承诺的回退。"""
    if not _pylint_installed():
        pytest.skip("pylint 未安装")
    path = _write_sample(str(tmp_path), LONG_LINE + "\n" + BRANCHY)

    _simulate_not_installed(monkeypatch)
    without_pylint = detect_python.collect([path], ccn_threshold=2)
    monkeypatch.undo()
    with_pylint = detect_python.collect([path], ccn_threshold=2)

    ast_rules = {"line-too-long", "ccn"}
    assert _rules(without_pylint) & ast_rules == _rules(with_pylint) & ast_rules
    assert _rules(without_pylint) & ast_rules == ast_rules


def test_collect_raises_when_no_python_files(tmp_path):
    """没有 .py 文件时必须抛 NoSourceFiles,不能返回空列表。

    空列表和"查过了,无违规"无法区分——路径打错会被门禁报成合规,
    这是检测器最危险的一种静默通过。断言的是异常类型,不是消息文案。
    """
    (tmp_path / "notes.txt").write_text("not python", encoding="utf-8")

    with pytest.raises(detect_python.NoSourceFiles):
        detect_python.collect([str(tmp_path)])


def test_unreadable_file_is_reported_not_dropped(tmp_path):
    """读不了的文件要报 error,不能悄悄不计入。

    返回空列表会让退出码停在 0,门禁把"没查"当成"查过了没问题"。
    """
    path = tmp_path / "locked.py"
    path.write_text("value = 1\n", encoding="utf-8")
    real_open = builtins.open

    def _deny(target, *args, **kwargs):
        if str(target) == str(path):
            raise OSError("permission denied")
        return real_open(target, *args, **kwargs)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(builtins, "open", _deny)
    try:
        findings = detect_python.collect([str(tmp_path)], use_pylint=False)
    finally:
        monkeypatch.undo()

    assert "error" in _rules(findings)


def test_syntax_error_file_is_reported_not_dropped(tmp_path):
    """语法错误的文件要报 error。pylint 在时报 E0001,不在时只有 AST 能发现。"""
    path = _write_sample(str(tmp_path), "def broken(:\n")

    findings = detect_python.collect([path], use_pylint=False)

    assert "error" in _rules(findings)


def test_pylint_stdout_decoded_as_utf8(monkeypatch):
    """pylint 的 stdout 必须按 UTF-8 解,不能沿用本地代码页。

    pylint 在 duplicate-code(R0801) 报告里会回显源码行。按 cp936 解时,
    源码里的 UTF-8 中文让读线程抛 UnicodeDecodeError、proc.stdout 变 None,
    调用方随即 AttributeError 整个崩掉——实测扫含中文注释的 Python 项目必崩。
    这里钉住 encoding / errors 两个参数,不真跑 pylint。
    """
    captured = {}

    def _capture(*_args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(detect_python.subprocess, "run", _capture)
    detect_python.pylint_findings(["sample.py"], 120)

    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"


BARE_EXCEPT = """\
try:
    pass
except:
    pass
"""


def test_bare_except_is_reported_as_broad_except(tmp_path):
    """裸 `except:` 必须报出来——它是宽捕获里最宽的一种。

    曾因 PYLINT_ALIASES 里没有 bare-except(W0702),样本里的裸 except 一条不报,
    而同文件的 `except Exception` 正常报:最该报的那一类反而不报。pylint 本身是报的,
    是归一化时被当成了"范围外规则"丢掉。
    """
    path = _write_sample(str(tmp_path), BARE_EXCEPT)

    findings = detect_python.collect([path])

    assert "broad-except" in _rules(findings)


def test_path_batches_never_drop_a_file(monkeypatch):
    """文件多到命令行超长时必须分批,且一个文件都不能少。

    Windows CreateProcess 上限 32767 字符。曾一次性把全部路径传给 pylint,项目够大时
    抛 WinError 206,被当成"pylint 不可用"退回 AST——broad-except / unused-variable /
    unused-import 三条规则静默丢失,退出码还是 0。实测 500 文件 + 深路径(命令行总长
    36247)→ 0 条明细、退出码 0;同一项目用相对路径(32747)→ 1000 条、退出码 1。
    所以判据是命令行总长,不是文件数——路径深时两百多个文件就触发。
    """
    monkeypatch.setattr(detect_python, "_CMDLINE_BUDGET", 120)
    base = ["python", "-m", "pylint"]
    paths = [f"C:/deep/directory/module_{i:03d}.py" for i in range(30)]

    batches = detect_python._path_batches(paths, base)

    assert [p for batch in batches for p in batch] == paths
    assert len(batches) > 1
    for batch in batches:
        # 只有"单条路径就超预算"才允许突破预算,那时让 pylint 自己报错
        assert len(batch) == 1 or detect_python._cmdline_len(base + batch) <= 120


def test_path_batches_single_oversized_path_still_emitted():
    """单条路径就超预算时单独成批——让 pylint 自己报错,比静默丢文件好。"""
    base = ["python", "-m", "pylint"]
    huge = "C:/" + "x" * 40000 + ".py"

    assert detect_python._path_batches([huge], base) == [[huge]]


def test_default_cmdline_budget_is_below_windows_limit():
    """默认预算必须低于 Windows 的 32767 字符上限,否则分批形同虚设。

    上面那条把 _CMDLINE_BUDGET  monkeypatch 小了测分批逻辑,这条守**默认值**——
    默认值一旦被调到上限之上,_path_batches 永远返回单批,WinError 206 立刻复发,
    而分批逻辑的测试一条都不会红。
    """
    assert 0 < detect_python._CMDLINE_BUDGET < 32767


UNUSED_IMPORT = """\
import os
import sys

print(sys.argv)
"""

UNUSED_VARIABLE = """\
def f():
    used = 1
    dead = 2
    return used
"""


def test_unused_import_is_reported(tmp_path):
    """未使用导入要报。

    变异测试实测:把 unused-import 的别名集清空,56 条测试照样全绿——
    这条 pylint 规则处于"删了没人发现"状态。
    """
    path = _write_sample(str(tmp_path), UNUSED_IMPORT)

    assert "unused-import" in _rules(detect_python.collect([path]))


def test_unused_variable_is_reported(tmp_path):
    """只赋值不读取的变量要报。

    变异测试实测:把 unused-variable 的别名集清空,56 条测试照样全绿。
    """
    path = _write_sample(str(tmp_path), UNUSED_VARIABLE)

    assert "unused-variable" in _rules(detect_python.collect([path]))


def test_complexity_base_is_one():
    """没有判定点的函数 CCN 必须是 1,不是 0。

    变异测试实测:把 _own_complexity 的基数从 1 改成 0,全仓库 CCN 集体少 1,
    而没有任何用例断言过具体数值——自扫仍显示 1 条 ccn,看着一切正常。
    """
    import ast

    tree = ast.parse("def trivial():\n    return 1\n")

    assert detect_python._own_complexity(tree.body[0]) == 1


def test_syntax_error_reported_once_not_twice(tmp_path):
    """同一个语法错误文件只报一条 error。

    pylint 报 E0001,AST 回退也报一条,两条路径不去重时一个坏文件算成
    "2 处违规"——条数对不上,且看着像两个独立问题。
    """
    path = _write_sample(str(tmp_path), "def broken(:\n    pass\n")

    findings = [f for f in detect_python.collect([path]) if f.rule == "error"]

    assert len(findings) == 1, findings


def test_ast_line_numbers_survive_form_feed(tmp_path):
    """AST 回退路径的行号也不能被 0x0C 带偏。

    `split_lines` 有三个调用点:scan_lines、这里的行宽、pylint 输出解析。
    变异测试实测只把这一处换回 str.splitlines(),测试照样全绿——另两个
    调用点没有自己的守军。
    """
    # 第 2 行是 130 字符;0x0C 在第 1 行。若按 splitlines 断行,超长行会报成第 3 行。
    body = "x = 1\n\x0c" + "y = " + repr("z" * 120) + "\n"
    path = _write_sample(str(tmp_path), body)

    findings = detect_python.collect([path], use_pylint=False)

    assert lines_of(findings, "line-too-long") == [2]


def test_max_line_length_is_passed_to_pylint(monkeypatch, tmp_path):
    """--max-line-length 必须真的传给 pylint。

    曾有一条用例想守"两种模式下 AST 可判规则结论一致",但样本行是 130 字符,
    在 pylint 默认 100 和本项目 120 之下都超限——对 100~120 区间的阈值漂移
    完全失明。这里直接断言参数传了过去。
    """
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    _write_sample(str(tmp_path), "x = 1\n")

    detect_python.collect([str(tmp_path)], max_line=137)

    assert "--max-line-length" in captured["cmd"]
    assert "137" in captured["cmd"]


def test_pylint_output_parsing_survives_form_feed(monkeypatch, tmp_path):
    """pylint 输出里的 0x0C 不能让一条明细变成两条。

    解析用 str.splitlines() 时会把它切成两半,两半各自都长得像一条明细,于是一条
    变两条;split_lines 按 pylint 的口径把它留在同一行。变异测试实测:把这一处换回
    splitlines(),本用例的发现数从 0 变成 2。

    这里断言"不增多"而不是具体条数:粘连行经贪婪 file 段解析后,file 段会变成
    `...(sym)` 后面跟着 0x0C 再跟一遍路径,这种垃圾串随后被
    test_findings_for_unscanned_files_are_dropped 守的那道过滤正确丢掉——所以修复后
    是 0 条。真正防 phantom 的是那道过滤,这条只保证 0x0C 不会让条数翻倍。
    """
    sample = _write_sample(str(tmp_path), "x = 1\n")
    echoed = (f"{sample}:2:0: C0301: Line too long (130/120) (line-too-long)"
              "\x0c "
              f"{sample}:3:0: C0301: Line too long (140/120) (line-too-long)")
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **kw: SimpleNamespace(stdout=echoed, stderr="",
                                         returncode=0))

    findings = detect_python.collect([str(tmp_path)])

    assert len(findings) <= 1, findings


def test_findings_for_unscanned_files_are_dropped(monkeypatch, tmp_path):
    """不是我们扫过的文件,它的"明细"必须被丢掉。

    `_PYLINT_LINE` 的 file 段是贪婪 `.+`,任何长得像 `xxx:N:M: CODE: msg (sym)`
    的行都会被当成一条发现——pylint 的输出里除了明细还有 Module 头、评分脚注,
    某些配置下还会回显源码行。按"我们真扫过哪些文件"收口一次,phantom 发现就进不来。
    """
    sample = _write_sample(str(tmp_path), "x = 1\n")
    echoed = (f"{sample}:2:0: C0301: Line too long (130/120) (line-too-long)\n"
              "some/other/file.py:9:0: C0301: Line too long (999/120) "
              "(line-too-long)\n"
              "************* Module sample\n")
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **kw: SimpleNamespace(stdout=echoed, stderr="",
                                         returncode=0))

    findings = detect_python.collect([str(tmp_path)])

    assert lines_of(findings, "line-too-long") == [2], findings


def test_report_output_is_sorted(monkeypatch, capsys):
    """明细必须按 (规则, 文件, 行号) 排序输出。

    变异测试实测:把 report() 里的 sorted(...) 去掉,输出顺序不确定,而没有任何
    用例断言过顺序——顺序乱掉时人读报告找不到重点,管道给其他工具也不稳定。
    """
    findings = [
        Finding("ccn", "b.py", 1, "x"),
        Finding("broad-except", "a.py", 9, "y"),
        Finding("broad-except", "a.py", 2, "z"),
    ]

    _common.report(findings, as_json=False)

    out = capsys.readouterr().out.strip().split("\n")
    assert len(out) == 3
    assert out[0].startswith("broad-except\ta.py:2\t")
    assert out[1].startswith("broad-except\ta.py:9\t")
    assert out[2].startswith("ccn\tb.py:1\t")


def test_dummy_variables_rgx_is_passed_to_pylint(monkeypatch, tmp_path):
    """--dummy-variables-rgx 必须真的传给 pylint。

    这个开关让检测器比 pylint 默认更严(`_unused` / `_idx` 也会报),docstring 里
    专门解释了它。变异测试实测:不传这条参数,56 条测试照样全绿——一个影响
    "检测器与项目自己的 pylint 配置是否一致"的开关无人看守。
    """
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    _write_sample(str(tmp_path), "x = 1\n")

    detect_python.collect([str(tmp_path)])

    assert "--dummy-variables-rgx" in captured["cmd"]
    assert "^_+$" in captured["cmd"]


def test_docstring_checks_are_disabled_for_pylint(monkeypatch, tmp_path):
    """missing-*-docstring 与 invalid-name 必须被禁用。

    这两类不在本规范范围内。变异测试实测:把 invalid-name 从 --disable 里去掉,
    会多报一批范围外规则,把"按规则聚合做前后对比"的基线搅乱。
    """
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    _write_sample(str(tmp_path), "x = 1\n")

    detect_python.collect([str(tmp_path)])

    disabled = captured["cmd"][captured["cmd"].index("--disable") + 1]
    for rule in ("missing-module-docstring", "missing-function-docstring",
                 "missing-class-docstring", "invalid-name"):
        assert rule in disabled, rule


def test_qualname_includes_full_ancestor_chain():
    """嵌套函数的限定名要走完整祖先链。

    曾用"第一个包含它的类"就停,于是类方法里的嵌套函数报成 `C.inner` 而不是
    `C.m.inner`——两个不同函数在报告里长得一模一样,按名字去找代码的人会找错。
    """
    import ast

    src = ("class C:\n"
           "    def m(self):\n"
           "        def inner():\n"
           "            pass\n"
           "        return inner\n")
    tree = ast.parse(src)
    inner = tree.body[0].body[0].body[0]

    assert detect_python._qualname(tree, inner) == "C.m.inner"


def test_qualname_distinguishes_same_named_nested_functions():
    """两个不同函数里的同名内函数,限定名必须能区分。"""
    import ast

    src = ("def a():\n"
           "    def x():\n"
           "        pass\n"
           "    return x\n"
           "\n"
           "def b():\n"
           "    def x():\n"
           "        pass\n"
           "    return x\n")
    tree = ast.parse(src)
    names = {detect_python._qualname(tree, n) for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef)}

    assert {"a", "b", "a.x", "b.x"} <= names, names


def test_pylint_findings_actually_batches(monkeypatch, tmp_path):
    """pylint_findings 必须真分批调用,不是把全部路径一次性传给 pylint。

    广谱变异扫描实测:把 `for batch in _path_batches(...)` 改成 `for batch in
    [paths]`,全套测试照样绿——此前只有 _path_batches 自身的单测(它 monkeypatch
    小了预算),没有一个用例走 pylint_findings 看它到底调了几次 subprocess。
    """
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    # 预算要大于 pylint 基础命令本身(约 380 字符),否则每条路径都单独成批,
    # 那是 _path_batches 对"单条就超预算"的既定行为,不是没分批。
    budget = 700
    monkeypatch.setattr(detect_python, "_CMDLINE_BUDGET", budget)
    for i in range(6):
        _write_sample(str(tmp_path), f"x{i} = 1\n", name=f"m{i}.py")

    detect_python.collect([str(tmp_path)])

    assert len(calls) > 1, f"只调用了 {len(calls)} 次 pylint，没有分批"
    # 每一批的命令行都不能超预算
    for cmd in calls:
        assert detect_python._cmdline_len(cmd) <= budget, cmd


def test_main_returns_2_when_no_python_files(tmp_path):
    """退出码 2 的契约:没查到任何文件不等于无违规。

    finish() 把 NoSourceFiles 映射成 2;这条用例同时守住那个映射。
    断言退出码本身,不断言 stderr 文本。
    """
    (tmp_path / "notes.txt").write_text("not python", encoding="utf-8")

    assert detect_python.main([str(tmp_path)]) == 2
