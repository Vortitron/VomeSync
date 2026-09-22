# An MCP key, before there is an account

*Integration side. The Vome side is `docs/agent_trial_plan.md` in the
portal repo.*

Pointing Cursor, VS Code or Claude at your own Home Assistant is the best
thing this add-on has to show a developer, and it used to be five steps
and a website away: sign in at vome.io, link this instance, open the
tokens page, tick the right box, copy a snippet. Each of those is a
reason to stop before seeing it work once.

So the button is in the panel, and it works with no account: **Coding
agent** → tick what it may do → **Issue a key** → paste the JSON.

## What happens when you press it

`vomesync.agent_key_issue` calls `POST /api/v1/relay/agent` — the one
call in this integration that carries no credential at all. Vome opens a
throwaway account, provisions the relay link, mints a key scoped to this
instance alone, and answers with the relay credentials plus a
paste-ready `mcp.json`. The tunnel comes up, and the key works a second
later.

The key is a VomeHome personal access token (`vh_…`), not a Home
Assistant long-lived token. That distinction is the whole point:

* it reaches **this** instance only, and only through Vome's broker;
* it can only do what was ticked here, enforced at Vome's end and
  written to an audit log;
* Home Assistant's own credentials never leave the house;
* sensitive domains (locks, alarms, covers, climate, …) are refused
  whatever the key says.

## It is a trial

What it grants is a brokered tunnel into a house, for somebody Vome
cannot identify and could never contact. So it covers one Home Assistant
and it **ends after two days**.

Three ways it can end, and two of them delete everything:

| | What happens |
|---|---|
| **Kept** | Connect a Vome account (panel → Vome account → Connect). The server *and the key* move to that account and the expiry is lifted — the `mcp.json` already pasted into an editor keeps working, and the house is not re-linked. |
| **Revoked** | Panel → Coding agent → Revoke. The key, the link and the throwaway account go now. |
| **Ignored** | The clock runs out and Vome deletes it. |

The expiry rides on the key itself and Vome checks it on every call, so
it stops working on time whether or not anything has been swept.

## Managing it without a browser

Everything after the first call is authenticated by the relay secret
this house already holds, so nothing here needs a website:

| Service | What it does |
|---|---|
| `vomesync.agent_key_state` | What the key grants and how long it has left. |
| `vomesync.agent_key_issue` | Issue one (unlinked houses only). |
| `vomesync.agent_key_scopes` | Re-grant it. **The key itself does not change**, so ticking a box never means pasting a new `mcp.json`. |
| `vomesync.agent_key_reissue` | Replace a lost key, inheriting its permissions *and its clock*. |
| `vomesync.agent_key_revoke` | End all of it. |

## The permissions

| Scope | What it lets an agent do |
|---|---|
| `ha:read` | Entities, states, history, logs, automations, the config check. Every other permission implies it. |
| `ha:write` | Call services. Sensitive domains stay refused. |
| `ha:config` | Create, change and delete automations, scripts, scenes and dashboards. |
| `ha:files` | Read and write files under `/config` — **including `secrets.yaml`**. |

`ha:files` is offered unticked. It reaches the place this instance keeps
its credentials, and on a trial there is nobody identified on the other
end of the grant. Read, write and config are what the button ticks for
you.

## What it deliberately does not do

It does **not** turn on UI forwarding. A guest health-score run does,
because its owner is handed a web address to open; an agent key needs no
such thing, and publishing this house's login page is not a side effect
anybody asked for.

It also refuses rather than overwriting. A house already holding a
temporary health-score link is asked to finish with that first, because
issuing over it would orphan the run at Vome's end. A house linked to a
real account is sent to **Account → API tokens**, where its keys can be
listed and revoked from anywhere.

## The key is shown once

Vome stores only a hash of it, and this integration stores no copy at
all — there is no second place to steal it from, and no way to re-show
it. That is what **Replace key** is for.
