#!/bin/bash
# SessionStart hook for Claude Code on the web: install Python dependencies so
# tests, linters and the agentovka MCP server (.mcp.json) start without delay.
set -euo pipefail

# Only needed in remote (web/mobile) sessions; local setups manage their own venv.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"
uv sync --group dev
