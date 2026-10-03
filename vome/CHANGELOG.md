# Changelog

## 0.3.53 — 3 October 2026

The Coding agent page offers all four of Claude Code's side panes, one
command each: automations, ESPHome, your home's health score (with a Fix
button on each finding) and a working dashboard with live states. The
bundled integration says 0.9.38, so it can be told apart from the one
before live states (0.3.52 carried them, still labelled 0.9.37).

## 0.3.52 — 3 October 2026

Live states for dashboards. When a dashboard pane in Claude Code (or any
client using Vome's MCP) shows this home, Vome can now ask the integration
to watch just the entities on show and send each change the moment Home
Assistant has it, over the link the integration already keeps, instead of
being asked for them every few seconds. It only reports states and never
changes anything, accepts entity ids and nothing else, and stops when the
dashboard closes. Works the same on a house linked to Vome and on a home
hosted by Vome.

## 0.3.51 — 2 October 2026

The Coding agent page offers Claude Code's second side pane beside the
automation one: the ESPHome pane, which maps the device Claude is working on
from its YAML (pins, sensors, what reacts to what) and shows a build or a
flash as it runs. One command each; both only read.

## 0.3.50 — 2 October 2026

A Home Assistant linked to a Vome account gets the same Claude Code steps on
the Coding agent page: create a key on the account, then one command in
Claude Code that asks for it. The page used to stop at "Open API tokens", and
its introduction promised a key and permissions below that were not there.

## 0.3.49 — 2 October 2026

The Coding agent page connects Claude Code in two steps instead of a JSON
block: a command to run in Claude Code (it installs Vome's connector and asks
for the key), then the key to paste. The JSON is still there for anyone who
prefers it, and the page offers the automation pane, which shows the
automation Claude is working on. Other agents are unchanged.

## 0.3.48 — 1 October 2026

The panel's CHAP page shows a local pair (two Home Assistants in your house,
Vome CHAP 0.2.1 or later) -- which one runs your home, whether the other
answers and is in step -- instead of "Not paired".

## 0.3.47 — 1 October 2026

**Security: the Vome panel answers only Home Assistant.**

The panel's port is not open to your network, but every app on the same Home
Assistant shares its internal network, and any of them could call the panel
directly — issue an agent key, unlink this install, add a LAN route —
without the Home Assistant sign-in that normally sits in front of it. The
panel now refuses anything that does not come through Home Assistant's own
ingress. Nothing changes for you: the panel opens from the sidebar as before.

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
