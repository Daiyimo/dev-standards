#!/usr/bin/env bash
# dev-standards 一键安装 / 更新脚本
# 幂等:未安装则安装,已安装则强制同步到最新版本。安装与更新是同一条命令。
# 用法:
#   curl -fsSL https://raw.githubusercontent.com/Daiyimo/dev-standards/main/install.sh | bash
# 可选:项目级安装 —— 覆盖目标 skills 目录
#   CLAUDE_SKILLS_DIR=/path/to/project/.claude/skills bash install.sh
set -euo pipefail

REPO_URL="https://github.com/Daiyimo/dev-standards.git"
BRANCH="main"
SKILL_NAME="dev-standards"
# 默认装到用户级 skills 目录;设置 CLAUDE_SKILLS_DIR 可改为项目级
SKILLS_DIR="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
DEST="$SKILLS_DIR/$SKILL_NAME"

if ! command -v git >/dev/null 2>&1; then
  echo "错误:未找到 git,请先安装 git 后重试。" >&2
  exit 1
fi

if [ -d "$DEST/.git" ]; then
  echo "==> 检测到已安装,更新到最新版本:$DEST"
  git -C "$DEST" fetch --depth 1 origin "$BRANCH"
  git -C "$DEST" reset --hard "origin/$BRANCH"
else
  if [ -e "$DEST" ]; then
    # 目录存在但不是 git 仓库(早期手动拷贝),备份后重装,避免污染用户数据
    BACKUP="${DEST}.bak.$(date +%Y%m%d%H%M%S)"
    echo "==> 发现非 git 的旧安装,已备份到:$BACKUP"
    mv "$DEST" "$BACKUP"
  fi
  echo "==> 安装 dev-standards 到:$DEST"
  mkdir -p "$SKILLS_DIR"
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$DEST"
fi

echo "完成。dev-standards 已就绪:$DEST"
echo "重启 Claude Code,或在对话中说「启动规范」即可激活。"
