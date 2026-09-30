#!/usr/bin/env bash
# dev-standards 一键安装 / 更新脚本
# 幂等:未安装则安装,已安装则强制同步到最新版本。安装与更新是同一条命令。
# 用法:
#   curl -fsSL https://raw.githubusercontent.com/Daiyimo/dev-standards/main/install.sh | bash
# 可选:项目级安装 —— 覆盖目标 skills 目录
#   CLAUDE_SKILLS_DIR=/path/to/project/.claude/skills bash install.sh
# 可选:从本地源码目录同步 —— 不联网、不需要先 push,用于改完源码立刻部署到本机
#   CLAUDE_SKILLS_SRC=/path/to/dev-standards bash install.sh
# 说明:默认路径从 GitHub 拉取,本地源码的未提交改动必须 commit + push 后才会生效;
#       不想先 push 就用上面这条本地同步。
set -euo pipefail

REPO_URL="https://github.com/Daiyimo/dev-standards.git"
BRANCH="main"
SKILL_NAME="dev-standards"
# 默认装到用户级 skills 目录;设置 CLAUDE_SKILLS_DIR 可改为项目级
SKILLS_DIR="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
DEST="$SKILLS_DIR/$SKILL_NAME"

case "$DEST" in
  */dev-standards) ;;
  *) echo "错误:目标目录名必须是 dev-standards:$DEST" >&2; exit 1 ;;
esac

if [ -n "${CLAUDE_SKILLS_SRC:-}" ]; then
  # 本地源码优先:直接同步,不联网、不要求先 push。用于"改完源码立刻部署到本机"。
  if [ ! -f "$CLAUDE_SKILLS_SRC/SKILL.md" ]; then
    echo "错误:CLAUDE_SKILLS_SRC 指向的目录没有 SKILL.md:$CLAUDE_SKILLS_SRC" >&2
    exit 1
  fi
  echo "==> 从本地源码同步:$CLAUDE_SKILLS_SRC -> $DEST"
  rm -rf "$DEST"   # 源码即权威,无需备份
  mkdir -p "$SKILLS_DIR"
  ( cd "$CLAUDE_SKILLS_SRC" && tar --exclude=.git -cf - . ) | tar -xf - -C "$DEST"
elif [ -d "$DEST/.git" ]; then
  if ! command -v git >/dev/null 2>&1; then
    echo "错误:未找到 git,请先安装 git 后重试。" >&2
    exit 1
  fi
  echo "==> 检测到已安装,从 origin/$BRANCH 更新:$DEST"
  git -C "$DEST" fetch --depth 1 origin "$BRANCH"
  git -C "$DEST" reset --hard "origin/$BRANCH"
else
  if ! command -v git >/dev/null 2>&1; then
    echo "错误:未找到 git,请先安装 git 后重试。" >&2
    exit 1
  fi
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
