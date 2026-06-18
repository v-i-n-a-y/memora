#!/usr/bin/env bash
# Install the Memora Claude Code plugin (hooks + skill) into ~/.claude.
#
# Registers the SessionStart + UserPromptSubmit + PostToolUse hooks by placing
# this plugin where Claude Code discovers it and enabling it in settings.json.
#
# No secrets: this script never reads or writes any server address or API key.
# Point the memora MCP server at your DB via claude-plugin/.mcp.json (local
# stdio) or your own ~/.claude.json entry (e.g. a remote HTTP server).
#
# Usage:
#   ./install.sh            # symlink (recommended for development)
#   ./install.sh copy       # copy instead of symlink
set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.claude/plugins/memora"
MODE="${1:-symlink}"

mkdir -p "$HOME/.claude/plugins"
[ -e "$DEST" ] || [ -L "$DEST" ] && rm -rf "$DEST"

case "$MODE" in
  copy)
    cp -r "$PLUGIN_DIR" "$DEST"
    echo "Copied plugin -> $DEST"
    ;;
  symlink|*)
    ln -s "$PLUGIN_DIR" "$DEST"
    echo "Symlinked plugin -> $DEST"
    ;;
esac

# Enable the plugin in settings.json (idempotent).
python3 - "$HOME/.claude/settings.json" <<'PY'
import json, os, sys
path = sys.argv[1]
try:
    with open(path) as f:
        settings = json.load(f)
except Exception:
    settings = {}
settings.setdefault("enabledPlugins", {})["memora@memora"] = True
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w") as f:
    json.dump(settings, f, indent=2)
print(f"Enabled memora@memora in {path}")
PY

echo ""
echo "Done. SessionStart + UserPromptSubmit + PostToolUse hooks load on the next session."
echo "Make sure the memora MCP server is configured (see claude-plugin/.mcp.json and README.md)."
