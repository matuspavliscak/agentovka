# Example configurations

## Claude Desktop

Copy the contents of [`claude_desktop_config.json`](claude_desktop_config.json)
into your Claude Desktop config file:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Fill in `ISDS_USERNAME` / `ISDS_PASSWORD` with **test** credentials
([datovka-test.gov.cz](https://www.datovka-test.gov.cz)) and restart Claude
Desktop.

## Claude Code

Register the server with the CLI:

```bash
claude mcp add agentovka \
  --env ISDS_USERNAME=your-test-username \
  --env ISDS_PASSWORD=your-test-password \
  --env ISDS_ENV=test \
  --env AGENTOVKA_ALLOW_SEND=false \
  -- uvx agentovka
```

## Any MCP client

Agentovka speaks MCP over **stdio**. Launch `uvx agentovka` (or `uv run
agentovka` from a clone) with the environment variables set, and point your
client at it as a stdio server.

> Keep `ISDS_ENV=test` and `AGENTOVKA_ALLOW_SEND=false` until you understand the
> [delivery semantics](../docs/delivery-semantics.md). Reading received messages
> legally delivers your mail and starts statutory deadlines.
