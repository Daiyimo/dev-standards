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

除此之外不自动激活；不确定时默认不激活。

## 文件结构

```text
dev-standards/
├── SKILL.md                    # 轻量主规则，功能优先
├── README.md                   # 使用和安装说明
├── CHANGELOG.md                # 规范演进记录
├── install.sh                  # 一键安装 / 更新（幂等）
├── uninstall.sh                # 一键卸载
└── references/                 # 明确需要时才读取的资料库
    ├── process.md              # 生产可观测性、文档交付、分支/PR
    ├── context-md.md           # CONTEXT.md 与 ADR
    ├── methodology.md          # 调试、TDD、原型、架构体检、知识收尾
    └── patterns.md             # 可选实现模式
```

参考文件不是自动生效的规范。不要在每次激活时全部读取，也不要因为参考中存在某种模式就自动把它加入项目。

## 两种模式

- **功能模式（默认）**：只检查与当前改动直接相关的风险，不输出完整合规清单。
- **严格复核模式**：仅在用户明确要求完整合规、发布前、安全或性能审计时使用，并限制在用户指定范围。

## 安装 / 更新 / 卸载

> 安装脚本需要 `git`，可在 macOS、Linux 和 Windows Git Bash 中使用。默认安装到用户级 `~/.claude/skills/dev-standards/`。

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

## 维护原则

- 触发条件只在 `SKILL.md` frontmatter 维护。
- 主文件只保留高频决策规则；长方法和模式留在 `references/`。
- 新规则必须说明它保护的真实功能或风险，不能只因为“最佳实践”就升级为默认强制项。
- 修改规范意图时，在 `CHANGELOG.md` 记录原因。
- 默认安装路径从 `origin/main` 拉取，本地源码的改动必须 commit + push 后才会对安装副本生效；不想先 push 就用 `CLAUDE_SKILLS_SRC` 从本地同步。
- 发布前确认源码目录与实际安装副本的 `SKILL.md`、`references/` 保持一致。
- 只装一处：不要在工作区里再放一份 `.claude/skills/dev-standards`，那会变成漂移源，出现"改了源码却没生效"。

## 灵感来源

部分可选方法论参考 [Matt Pocock's Skills](https://github.com/mattpocock/skills) 与 [khazix-skills / neat-freak](https://github.com/KKKKhazix/khazix-skills)。这些资料只在对应工作流被明确选择时使用。
