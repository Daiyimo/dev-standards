# dev-standards

一个**功能优先、按风险启用**的 Claude Code 工程复核 skill。

## 核心取向

- 默认先交付用户可见功能和验收标准。
- 普通新功能、一般 Bug 修复、常规重构不自动加载本 skill。
- 安全、性能、可观测性和完整工程流程只在有真实风险或用户明确要求时启用。
- 不为假想需求增加框架、抽象层、依赖或防御性代码。
- 验证以本次改动的主路径和回归风险为中心，不追求统一覆盖率或清单打卡。

## 何时激活

触发条件以 `SKILL.md` frontmatter 的 `description` 为唯一权威。简要来说：

- 用户明确要求启用规范、完整合规检查、安全/性能审计或发布前检查。
- 当前改动确实触及认证、授权、支付、真实隐私数据、密钥、破坏性迁移或公开不可信输入。
- 用户给出项目路径要求按规范整理、弄合规、统一风格或过一遍扫描规范——此时走 `SKILL.md` 第 13 节执行层。

除此之外不自动激活；不确定时默认不激活。仅给出项目路径而未要求合规时同样不激活。

## 文件结构

```text
dev-standards/
├── SKILL.md                    # 轻量主规则，功能优先；第 13 节是执行层
├── README.md                   # 使用和安装说明
├── CHANGELOG.md                # 规范演进记录
├── .gitignore                  # 排除 __pycache__ / .pytest_cache / 评审临时目录
├── install.sh                  # 一键安装 / 更新（幂等）
├── uninstall.sh                # 一键卸载
├── scripts/                    # 第 13 节执行层的检测器（只检测，不修改代码）
│   ├── detect.py               # 唯一入口，按语言分发并合并结果
│   ├── _common.py              # 共用基础设施（Finding / report / 扫描器 / 读文件）
│   ├── detect_python.py        # pylint 适配 + 内置 AST（行宽 / CCN），规则名归一化
│   ├── detect_go.py            # Go 规则（包注释 / 导出符号 / 行宽 / 函数规模）
│   ├── detect_cpp.py           # C/C++ 行宽 / 函数规模 / BackwardNull 候选
│   ├── _testhelpers.py         # 测试共用的写入与断言助手
│   └── test_detect_*.py        # 各层回归测试
└── references/                 # 明确需要时才读取的资料库
    ├── process.md              # 生产可观测性、文档交付、分支/PR
    ├── context-md.md           # CONTEXT.md 与 ADR
    ├── methodology.md          # 调试、TDD、原型、架构体检、知识收尾
    ├── patterns.md             # 可选实现模式
    ├── lint-remediation.md     # 静态扫描告警的修复规范（附录 D）
    ├── standards-registry.md   # 规则注册表：阈值、检测方式、覆盖边界
    └── ccn-splitting.md        # 高圈复杂度拆分手册、保真清单、差分测试
```

参考文件不是自动生效的规范。不要在每次激活时全部读取，也不要因为参考中存在某种模式就自动把它加入项目。

## 检测器

第 13 节执行层的机械层。给它一个项目路径，它自己检测违规，不要求使用者先导出扫描报告；它只检测，不修改代码。

```bash
# 检测（自动判别 Python / Go / C++，混合栈会都跑一遍并合并结果）
python scripts/detect.py /path/to/project

# 只修某一类，修完复检这一类
python scripts/detect.py --lang python --rules broad-except /path/to/project

# 全量复检 + JSON
python scripts/detect.py --json /path/to/project
```

退出码：`0` 无违规、`1` 有违规、`2` 自身出错。返回 2 的情形有三种——路径下没有目标语言源文件（没查到任何文件不等于合规，门禁要靠这个区分）、`--rules` 传了规则表外的名字、检测器自身抛异常。明细输出每行一条 `<规则名>\t<文件>:<行>\t<说明>`。

检测器自身有回归测试。测试盯的是“它不报真话”的路径（崩掉、静默跳过、误报），不是覆盖率数字——曾因两条静默漏报路径对满是违规的文件报绿灯：

```bash
python -m pytest scripts/ -q
```

### 各语言检测能力

| 语言 | 覆盖的规则 | 边界 |
| --- | --- | --- |
| Python | `broad-except`、`line-too-long`、`unused-variable`、`unused-import`、`ccn`、`error` | 需要 pylint；装不上自动退回内置 AST 检测（只剩行宽 + CCN + error） |
| Go | `golint/package`、`funccomment`、`structcomment`、`interfacecomment`、`varcomment`、`tosa/linelength`、`golint/fnsize`、`error` | 轻量行扫描器，假设代码经过 gofmt。项目有 golangci-lint / revive 时以它们为准 |
| C/C++ | `tosa/linelength`、`golint/fnsize`、`BackwardNull`、`error` | `BackwardNull` 只报候选，已排除“判空后提前返回”这一安全惯用法，必须人工确认 |

`error` 不是某条规范，是检测器自报的“这个文件读不了 / 解析失败”。它在三个检测器里都会产出，**最该被看见**，所以单列说明：按 registry 的规则名用 `--rules` 筛的时候，如果只想看规范违规而要排除它，就显式排除，别指望它不在。

源文件编码按 UTF-8 → GBK → latin-1 顺序探测。按 UTF-8 硬解 + `errors="replace"` 会让 GBK 中文每字变成两个 U+FFFD，行宽按字符数一算就虚高——实测一条真实 94 字符的 C++ 行被判成 154 字符。

### 已知边界

- 规则样本覆盖 C++ / Go / Python 三个栈。其他语言（Java、JS/TS、Rust）的规则名与阈值未经验证，换栈时先反推该栈的扫描配置再套用方法。
- CCN 检测是 Python AST 实现，只适用于 Python。
- `detect_go.py` 不是 Go 解析器，是行扫描器；遇到非常规格式可能漏报。
- `detect_cpp.py` 的 `BackwardNull` 是候选级，不能替代 clang-tidy 之类的分析器。
- C++ 侧：单行函数体（`int f() { return 0; }`）不被识别、构造函数初始化列表的函数名取到最后一个初始化成员、多后缀签名（`const noexcept override`）漏检。**文档注释不会算进 fnsize**——实测五种形态（无注释 / 单行 `/** */` / 多行 / `//` / 注释后空行）函数规模都是 5 行，`_signature_start` 停在签名首行。
- Windows 下把输出重定向到文件或管道时，Python 按本地代码页编码（简体中文环境是 cp936），按 UTF-8 读会乱码；直接看控制台不受影响（走 `WriteConsoleW`）。需要管道或落盘时设 `PYTHONIOENCODING=utf-8`，或先 `chcp 65001`。检测器读源文件不受此影响——它自己探测编码。**这条同时覆盖 stderr**：聚合统计和错误提示（`detect: 3 处违规`）走 stderr，重定向后同样是 cp936；`--json` 例外，它固定按 UTF-8 写，正是为了能安全地管道给机器。

### 已知缺陷（已复现，未修）

当门禁用之前先知道这几条，它们都往“漏报 / 误报 + 退出码不变”方向坏：

- **C++ 跨行字符串破坏花括号深度。** `_common.scan_lines` 的 `in_string` 是**每行局部变量**，不跨行延续（而 `in_block_comment` 跨行）。于是两种写法都会让深度失真：
  - C++11 raw string `R"( ... { ... )"` —— 字符串里的 `{` 被计入深度，实测报出 phantom 函数（函数名来自字符串内部）且真实函数区间被拉到 EOF；
  - 普通的反斜杠续行字符串 `const char* s = "abc\` 换行 `def } ghi";` —— 同理，实测 5 行函数被截成 3 行。

  两条同根，修任一条都要改 `scan_lines` 的词法状态机，而它同时服务 Go 与 C++，改动面大于其余几条，故留待单独一次整改。
- **`SKIP_DIRS` 被无提示跳过。** `build` / `dist` / `vendor` / `testdata` 等目录下的源文件不扫，退出码不变——和上面那条是同一种坏法（少查一批文件而门禁看不出），只是触发条件是目录名而不是文件内容。
- **pylint 输出里的源码行可能被误判成明细。** `_PYLINT_LINE` 的 file 段是贪婪 `.+`，而 pylint 在 duplicate-code（R0801）里会回显源码行；那一行里只要出现 `xxx:N:M: CODE: msg (sym)` 的形状就会被当成一条真发现。与行尾字符无关，是解析判据太宽。

已修（曾经的已知缺陷，修复均有测试守住，回退即变红）：pylint 分批（Windows 命令行超长时曾静默丢三条规则且退出码 0）、裸 `except:` 漏报、`str.splitlines()` 断行偏移、`--rules` 传不存在规则名返回 0、`--json` 重定向后不是 UTF-8、顶层 Go 接口被报成 `structcomment`。

## 三种工作方式

- **功能模式（默认）**：只检查与当前改动直接相关的风险，不输出完整合规清单。
- **严格复核模式**：仅在用户明确要求完整合规、发布前、安全或性能审计时使用，并限制在用户指定范围。
- **执行层（第 13 节）**：用户给出项目路径要求合规整改时，由 `scripts/detect.py` 检测、按七步流程整改并复检。见上面「检测器」一节。

## 安装 / 更新 / 卸载

> 在线安装与更新需要 `git` 和 `curl`；从本地源码同步（`CLAUDE_SKILLS_SRC`）两者都不需要。脚本可在 macOS、Linux 和 Windows Git Bash 中使用。默认安装到用户级 `~/.claude/skills/dev-standards/`。

### 一句话安装（也是更新命令）

```bash
curl -fsSL https://raw.githubusercontent.com/Daiyimo/dev-standards/main/install.sh | bash
```

脚本是幂等的：未安装时安装，已安装时同步到最新版本。更新后建议新开 Claude Code 会话，避免旧版 skill 内容仍留在当前会话上下文中。

### 项目级安装

```bash
CLAUDE_SKILLS_DIR=/path/to/project/.claude/skills \
  bash <(curl -fsSL https://raw.githubusercontent.com/Daiyimo/dev-standards/main/install.sh)
```

### 从本地源码同步（开发用）

改完源码不想先 push 就要部署到本机时：

```bash
CLAUDE_SKILLS_SRC=/path/to/dev-standards bash install.sh
```

从本地源码目录直接同步到 `CLAUDE_SKILLS_DIR`（默认用户级），不联网、不读 GitHub。
只在本机有效；换机器或分享给别人仍然用远端那几条。

### 一句话卸载

```bash
curl -fsSL https://raw.githubusercontent.com/Daiyimo/dev-standards/main/uninstall.sh | bash
```

项目级卸载需要设置与安装时相同的 `CLAUDE_SKILLS_DIR`。脚本只移除目标 `dev-standards` 目录，不触碰其他 skill。

### 让 Agent 安装

在 Claude Code 中直接说：

> 帮我安装 dev-standards：`https://raw.githubusercontent.com/Daiyimo/dev-standards/main/install.sh`

### 手动安装

整个目录是自包含的，直接复制到以下任一位置：

- 用户级：`~/.claude/skills/dev-standards/`
- 项目级：`<项目>/.claude/skills/dev-standards/`

目录名需保持为 `dev-standards`。

## 依赖

- skill 本身不依赖 superpowers、subagent 或 Dynamic Workflow。
- `install.sh` / `uninstall.sh` 使用 Bash；在线安装和更新需要 `git`、`curl`。
- `scripts/` 检测器需要 Python 3.7+；`pylint` 可选——装上才覆盖 `broad-except` / `unused-*`，装不上自动退回内置 AST 检测（只剩行宽 + CCN），只有一行 stderr 提示。Go / C++ 检测器不依赖 Go 或 clang 工具链。

## 维护原则

- 触发条件只在 `SKILL.md` frontmatter 维护。
- 主文件只保留高频决策规则；长方法和模式留在 `references/`。
- 新规则必须说明它保护的真实功能或风险，不能只因为“最佳实践”就升级为默认强制项。
- 修改规范意图时，在 `CHANGELOG.md` 记录原因。
- `scripts/` 与 `references/` 的规则、阈值或检测能力变化同样记进 `CHANGELOG.md`；提交前跑一遍 `python -m pytest scripts/ -q` 和 `python scripts/detect.py .`。后者在本仓库**不要求归零**——自扫固定有 1 处 `ccn`（`scripts/_common.py` 的 `scan_lines`，圈复杂度 26，docstring 里声明有意不拆），退出码 1 是预期结果；要盯的是“改动前后计数一致、没有新增规则类别”。
- 默认安装路径从 `origin/main` 拉取，本地源码的改动必须 commit + push 后才会对安装副本生效；不想先 push 就用 `CLAUDE_SKILLS_SRC` 从本地同步。
- 发布前确认源码目录与实际安装副本的 `SKILL.md`、`references/`、`scripts/` 保持一致。
- 只装一处：不要在工作区里再放一份 `.claude/skills/dev-standards`，那会变成漂移源，出现"改了源码却没生效"。

## 灵感来源

部分可选方法论参考 [Matt Pocock's Skills](https://github.com/mattpocock/skills) 与 [khazix-skills / neat-freak](https://github.com/KKKKhazix/khazix-skills)。这些资料只在对应工作流被明确选择时使用。
