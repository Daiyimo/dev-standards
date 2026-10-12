#!/usr/bin/env python3
"""detect.py 入口的回归测试。

这个文件补的是**入口层**契约,此前四个 test_detect_*.py 没有一个 import detect:
auto 语言判别、--rules 过滤、--rules 传错规则名、--json 的编码、退出码 2 的映射
全部裸奔。实测把 detect_languages 改成恒返回 []、把 --rules 过滤整段删掉,41 条
测试照样全绿——入口是唯一被测代码够不着的一层。

跑法: python -m pytest scripts/test_detect.py -q
"""

from __future__ import annotations

import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# pylint: disable=wrong-import-position
import _common  # noqa: E402
import detect  # noqa: E402
import detect_python  # noqa: E402
from _common import Finding  # noqa: E402
from _testhelpers import write_source  # noqa: E402


class _FakeStdout:
    """只有 .buffer 和 write 的 stdout 替身,用来接 report() 的输出。"""

    def __init__(self) -> None:
        self.buffer = io.BytesIO()
        self.written: list[str] = []

    def write(self, text: str) -> int:
        self.written.append(text)
        return len(text)


VIOLATING_PY = """\
try:
    pass
except Exception:
    pass
"""


def test_unknown_rule_name_returns_2(tmp_path):
    """--rules 传了规则表外的名字必须报自身错误。

    曾返回 0:"筛掉之后没剩"和"本来没违规"是同一个退出码,第 13 节第 4 步
    "修完复检这一类"会因一个拼写错误变成假绿。和 NoSourceFiles 要防的是同一类
    静默通过,只是触发条件从路径打错换成规则名打错。
    """
    write_source(tmp_path, "a.py", VIOLATING_PY)

    assert detect.main(["--rules", "no-such-rule", str(tmp_path)]) == 2


def test_known_rule_name_still_filters(tmp_path):
    """规则名正确时过滤照旧工作,不会因上面那条校验被误伤。"""
    write_source(tmp_path, "a.py", VIOLATING_PY)

    assert detect.main(["--rules", "broad-except", str(tmp_path)]) == 1
    assert detect.main(["--rules", "ccn", str(tmp_path)]) == 0


def test_known_rules_matches_registry():
    """KNOWN_RULES 要和 references/standards-registry.md 第 1 节**相等**。

    校验用的是这份集合;它和注册表漂移时,--rules 会把合法规则名判成非法(或少校验一类)。
    用相等而不是子集:只查"注册表 ⊆ KNOWN_RULES"时,注册表新增一条规则而 KNOWN_RULES
    没跟上,测试照样绿——而那条规则随后就筛不动。
    """
    registry = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "references", "standards-registry.md"))
    if not os.path.exists(registry):               # 从别处跑测试时跳过
        pytest.skip("找不到 standards-registry.md")
    with open(registry, encoding="utf-8") as handle:
        text = handle.read()
    table = text.split("## 1. 规则注册表")[1].split("## ")[0]
    listed = {line.split("`")[1] for line in table.splitlines()
              if line.startswith("| `")}

    assert listed == detect.KNOWN_RULES, (
        f"只在 registry: {sorted(listed - detect.KNOWN_RULES)}; "
        f"只在 KNOWN_RULES: {sorted(detect.KNOWN_RULES - listed)}")


def test_auto_mode_returns_2_when_no_source_files(tmp_path):
    """auto 模式没找到源文件时返回 2,不是 0。

    这条契约此前只有三个语言检测器各自守着,detect.py 这一层没有。
    """
    (tmp_path / "notes.txt").write_text("not source", encoding="utf-8")

    assert detect.main([str(tmp_path)]) == 2


def test_json_output_is_utf8_regardless_of_stdout_encoding(monkeypatch):
    """JSON 必须按 UTF-8 落盘,不能随 stdout 的本地代码页。

    print() 在简体中文 Windows 上按 cp936 编码,重定向出的字节不是合法 UTF-8,
    消费端 json.loads 直接 UnicodeDecodeError——比乱码更糟,管道整个断掉。
    只修 JSON 这一条路:文本明细仍走 print(),GBK 控制台才显示得对。
    """
    fake = _FakeStdout()
    monkeypatch.setattr(sys, "stdout", fake)

    _common.report([Finding("golint/fnsize", "a.cpp", 1, "函数 big 的行数是 88")],
                   as_json=True)

    raw = fake.buffer.getvalue()
    payload = json.loads(raw.decode("utf-8"))
    assert payload[0]["message"] == "函数 big 的行数是 88"
    assert raw.endswith(b"\n")


def test_text_output_does_not_touch_stdout_buffer(monkeypatch):
    """文本明细仍走 print(),不写 .buffer——GBK 控制台的显示不能被打断。"""
    fake = _FakeStdout()
    monkeypatch.setattr(sys, "stdout", fake)

    _common.report([Finding("golint/fnsize", "a.cpp", 1, "函数 big 的行数是 88")],
                   as_json=False)

    assert fake.buffer.getvalue() == b""
    assert fake.written, "文本明细没走 print()"


def test_detector_crash_returns_2_not_1(monkeypatch, tmp_path):
    """检测器自身抛异常时必须返回 2,不能和"有违规"共用 1。

    实测曾让 main() 直接向上抛 RuntimeError,CLI 退出码是 1——一个崩掉的检测器
    和一份几千条的违规清单返回同一个码,门禁把"没查完"当成"查过了"。
    """
    write_source(tmp_path, "a.py", VIOLATING_PY)

    def boom(*args, **kwargs):
        raise RuntimeError("simulated detector crash")

    monkeypatch.setattr(detect_python, "collect", boom)

    assert detect.main(["--lang", "python", str(tmp_path)]) == 2


def test_finish_also_maps_crash_to_2(monkeypatch, tmp_path):
    """三个语言入口共用的 finish() 也要把崩溃转成 2。"""
    write_source(tmp_path, "a.py", VIOLATING_PY)

    def boom(*args, **kwargs):
        raise ValueError("simulated crash")

    monkeypatch.setattr(detect_python, "collect", boom)

    assert detect_python.main([str(tmp_path)]) == 2


def test_json_survives_broken_pipe(monkeypatch):
    """读端提前走掉(--json | head)不能吐 traceback。

    实测 1200 文件项目 `detect.py --json | head -c 20` 三次全抛 BrokenPipeError,
    stderr 500+ 字节 traceback。第一轮把 payload 合并成单次 write 后更容易触发。
    """

    class _BrokenStdout:
        class buffer:
            @staticmethod
            def write(_payload):
                raise BrokenPipeError("reader went away")

            @staticmethod
            def flush():
                pass

        def fileno(self):
            raise ValueError("no fd")

    monkeypatch.setattr(sys, "stdout", _BrokenStdout())

    assert _common.report([Finding("golint/fnsize", "a.cpp", 1, "函数 big")],
                          as_json=True) == 1


def test_collect_files_empty_skip_dirs_means_skip_nothing(tmp_path):
    """skip_dirs=set() 表示什么都不跳过,不能静默回落成默认 SKIP_DIRS。

    `skip_dirs or SKIP_DIRS` 里空集合是 falsy,会被 or 吃成默认值。
    """
    (tmp_path / "build").mkdir()
    write_source(tmp_path / "build", "a.py", "x = 1\n")

    found = _common.collect_files([str(tmp_path)], (".py",), skip_dirs=set())

    assert any(f.endswith("a.py") for f in found)


def test_collect_files_default_still_skips_build(tmp_path):
    """对照:不传 skip_dirs 时 build/ 仍然要跳过。"""
    (tmp_path / "build").mkdir()
    write_source(tmp_path / "build", "a.py", "x = 1\n")

    found = _common.collect_files([str(tmp_path)], (".py",))

    assert not any(f.endswith("a.py") for f in found)


def test_json_mode_returns_1_when_there_are_violations(monkeypatch):
    """--json 有违规时退出码必须是 1,不能是 0。

    广谱变异扫描实测:把 report() 的 JSON 分支改成恒返回 0,全套测试照样绿——
    那条 return 只在 as_json=True 时走到,而此前只有文本路径的退出码有人守。
    门禁用 --json 管道时会拿到"全绿"的假象。
    """
    fake = _FakeStdout()
    monkeypatch.setattr(sys, "stdout", fake)

    rc = _common.report([Finding("golint/fnsize", "a.cpp", 1, "函数 big")],
                        as_json=True)

    assert rc == 1


def test_json_mode_returns_0_when_clean(monkeypatch):
    """对照:--json 无违规时退出码是 0。"""
    fake = _FakeStdout()
    monkeypatch.setattr(sys, "stdout", fake)

    assert _common.report([], as_json=True) == 0


def test_cli_accepts_json_flag(tmp_path):
    """命令行必须真的接受 --json。

    广谱变异扫描实测:把 build_parser 里的 --json 参数删掉,全套测试照样绿——
    现有 JSON 用例都直接调 _common.report(),没有一个走命令行解析。
    """
    write_source(tmp_path, "a.py", VIOLATING_PY)

    assert detect.main(["--json", "--lang", "python", str(tmp_path)]) == 1


def test_collect_files_accepts_a_file_path(tmp_path):
    """传**文件**路径而不是目录时必须能被收下。

    广谱变异扫描实测:把 collect_files 里 `os.path.isfile(path)` 那个分支删掉,
    全套测试照样绿——所有用例都传目录。detect.py <单文件> 会直接报"没有源文件"。
    """
    path = write_source(tmp_path, "solo.py", "x = 1\n")

    assert _common.collect_files([path], (".py",)) == [path]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
