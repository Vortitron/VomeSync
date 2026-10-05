# Changelog

## 0.3.62 — 5 October 2026

End-to-end remote access now has the Vome door, held by your Home
Assistant itself (integration 0.9.47). Vome cannot
see an end-to-end session, so your home checks what Vome checks on your
usual address: a Vome sign-in, your door password, or the access you
chose (app access open, webhooks), and sends anyone else to the Vome gate.
It limits how often a stranger may try, blocks an address after five
failed logins in 15 minutes, keeps Vome's own sign-in cookie away from
Home Assistant, and reports all of it to your access log, with the
visitor's real address. Still off until Vome turns it on for your home.

## 0.3.61 — 4 October 2026

End-to-end remote access gets a new certificate when Vome switches the
certificate authority it uses, not only when the name changes or the old
one is about to expire (integration 0.9.46). Found on the test home: it
kept a Let's Encrypt staging certificate, which a browser refuses on
vome.io with no way past.

## 0.3.60 — 4 October 2026

End-to-end remote access no longer reads its certificate on Home
Assistant's event loop, which Home Assistant warned about (integration
0.9.45). It does not change what it does.

The integration's Configure menu has a Coding agent page: the free key
for Claude Code, Cursor and VS Code, with its permissions and the
commands to paste (Claude Code's side panes included), for installs
through HACS that have no Vome panel.

## 0.3.59 — 4 October 2026

Groundwork for end-to-end encrypted remote access (integration 0.9.43),
dormant until Vome turns it on for a home. When it is on, your home gets
its own certificate for an address under e2e.vome.io, with a key made in
and never leaving your Home Assistant, and connections to that address
are decrypted only here. Vome passes them on without being able to read
them. Nothing changes until then.

## 0.3.58 — 4 October 2026

A Vome-hosted home restored from a physical install keeps its add-on linked
to the house's original Vome connection. Asked to link again, the add-on now
says which connection that is (integration 0.9.42), so Vome can use it for
the hosted home's config files and ESPHome. Before, those tools said "no
component linked" on such a home. Nothing is re-pointed, and the reply
carries the connection's id, never its secret.

## 0.3.57 — 4 October 2026

A user marked "Can only log in from the local network" now stays local
when you reach Home Assistant through your Vome address (integration
0.9.41). Before, Home Assistant saw every request through Vome as coming
from inside the house, so such a user could sign in from anywhere if they
got past the Vome door. That includes the service logins Vome creates for
add-ons. Vome now refuses them at sign-in, on the live connection and on
signed camera links, and a refused sign-in leaves no working login behind.

## 0.3.56 — 4 October 2026

Cameras that stream over WebRTC can get a relay for when your phone and
Home Assistant cannot reach each other directly, such as on mobile data
or behind a carrier's NAT (integration 0.9.40). Vome issues this home a
login for a TURN server that lasts a day and hands it to Home Assistant,
which passes it to both the browser and go2rtc. Until Vome turns this on
for your home nothing changes, and cameras stream as before.

## 0.3.55 — 4 October 2026

Your coding agent can delete a file it no longer needs from the config
directory (integration 0.9.39). It deletes one file at a time, never a
folder. It refuses configuration.yaml, secrets.yaml and Home Assistant's
database outright, including through a link with another name. It stays
inside the config directory and out of `.storage`, like reads and writes.
It needs the same ha:files permission as editing files.

## 0.3.54 — 3 October 2026

The Coding agent page offers all four Claude Code panes in one command
(`vome-panes`), before the panes one by one.

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
