#!/usr/bin/env bash
# dev-standards 卸载脚本:干净移除已安装的 skill,不触碰其他 skill 或系统环境。
# 用法:
#   curl -fsSL https://raw.githubusercontent.com/Daiyimo/dev-standards/main/uninstall.sh | bash
# 可选:与安装时一致的 CLAUDE_SKILLS_DIR(项目级安装需指定同一路径)
set -euo pipefail

SKILL_NAME="dev-standards"
SKILLS_DIR="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
DEST="$SKILLS_DIR/$SKILL_NAME"

if [ -d "$DEST" ]; then
  rm -rf "$DEST"
  echo "已卸载:$DEST"
else
  echo "未发现安装:$DEST(无需卸载)"
fi
