# Changelog

## 0.3.46 — 1 October 2026

**Coding-agent keys now come in your agent's own format: OpenCode and VS Code
included.**

Agents don't share one MCP config file, and until now every one of them was
handed the block written for Cursor. OpenCode refuses that block outright
(its config has no `mcpServers`), and VS Code's `.vscode/mcp.json` wants
`servers` instead. If you made a key for OpenCode or VS Code and it never
connected, this is why — sorry.

- The Coding agent page (Issue a key, Replace key) shows the config for
  Cursor / Claude Code, VS Code or OpenCode: pick yours, copy, paste.
- For OpenCode it is `opencode.json` with an `mcp` block, `"type": "remote"`
  and `"oauth": false`. That last line matters: without it OpenCode tries to
  sign in with OAuth, which Vome doesn't use, instead of sending your key.
- A Home Assistant linked to a Vome account gets its keys from
  [vome.io → Account → API tokens](https://vome.io/account/api-tokens), which
  has the same picker. An existing key keeps working: only the file around
  it changes.
- In OpenCode, "SSE error: Non-200 status code (405)" usually means Vome
  turned the key away. Check that it is pasted whole and that it has access
  to this Home Assistant.

## 0.3.45 — 30 September 2026

- Camera stills cross the relay, so an agent that takes a snapshot can
  look at it.
