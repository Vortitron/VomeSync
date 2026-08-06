# Plan: Home Assistant 2026.8 compatibility + Connect vs Nabu Casa Cloud

Written 2026-08-06. Research done; **no code changed yet**. Everything below is a
to-do list for the next session.

Sources:
[2026.8 release notes](https://www.home-assistant.io/blog/2026/08/05/release-20268/) ·
[device registry dev blog](https://developers.home-assistant.io/blog/2026/07/21/device-registry-single-config-entry) ·
[port 80 on existing installs (core#177585)](https://github.com/home-assistant/core/issues/177585) ·
[backup agent platform](https://developers.home-assistant.io/docs/core/platform/backup) ·
[Nabu Casa pricing](https://www.nabucasa.com/pricing/) ·
[HA Cloud integration](https://www.home-assistant.io/integrations/cloud/)

---

## Part A — 2026.8 compatibility

### A1. Hardcoded `127.0.0.1:8123` — ✅ DONE 2026-08-06 (one item deferred)

**Implemented:** `resolve_local_core_url()` in `relay_client.py` derives the URL
from the running instance; `RelayClient.local_url` is a property that re-resolves
on every use, so a port change (or the five-minute auto-rollback) is picked up
without a restart. Resolution order: explicit `local_url` override →
`hass.http.server_port` → `hass.config.api.port` → `hass.config.internal_url` →
the constant. Scheme follows `ssl_certificate` / `use_ssl`; a TLS-terminating
instance prefers `internal_url` because `https://127.0.0.1:port` fails cert
verification. Bind-to-one-interface installs dial the bound address rather than
loopback (same `server_host[0]` heuristic HA itself uses). Resolution is wrapped
in `suppress(Exception)` — it can never be the reason the relay stops working.
`async_start_relay` now logs the resolved URL and whether it was overridden or
auto-detected. 25 new tests; full suite 302 passed, 1 skipped. Add-on copy
re-synced via `vome/build.sh`.

**Still to do:** expose the `local_url` override in the panel (see point 3
below) — the override works, but only by editing config entry options directly,
so support has no self-serve way to correct a bad derivation. Versions were
deliberately **not** bumped; do that with A2 as one release.

The original analysis follows, for the record.

#### Original analysis

**The change.** HA now listens on **port 80** by default. The setting moved into
the UI at Settings → System → Network (alongside listen interface and trusted
proxies), with a five-minute auto-rollback if the user doesn't confirm the new
config still works. New HAOS installs get port 80 by default; per core#177585
**some existing installs are being offered — and in cases given — the switch**,
so this is not only a fresh-install concern.

**What breaks.** [`custom_components/vomesync/const.py:66`](../custom_components/vomesync/const.py#L66)
hardcodes:

```python
DEFAULT_LOCAL_CORE_URL = "http://127.0.0.1:8123"
```

`CONF_RELAY_LOCAL_URL` (`"local_url"`) is **never written anywhere in the
codebase** — grep confirms it is only *defined* in `const.py:40` and *read* in
[`relay_client.py:1126`](../custom_components/vomesync/relay_client.py#L1126)
(`local_url=relay.get(CONF_RELAY_LOCAL_URL)`). There is no options-flow step, no
service, and no panel field that sets it. So every Connect install falls through
to the hardcoded constant.

Everything downstream of `self._local_url` therefore points at a dead port on a
port-80 install:

- `relay_client.py:439` — REST dispatch (`self._local_url.rstrip("/") + path`)
- `relay_client.py:540` / `:584` — WS bridge via `_to_ws_url`
- `relay_client.py:161` — `_to_ws_url` fallback
- `relay_client.py:797` — the forward-UI target tuple

**Failure shape is the dangerous part:** the relay still connects outbound to
sync.vome.io and still reports healthy. The panel shows green. Every dispatched
request just fails. Silent, total loss of remote access for Connect subscribers
with a status page saying everything is fine.

**Also latent today:** an HA configured for SSL on the loopback has exactly the
same bug — we always assume plain `http`. Fixing A1 properly fixes that too.

**Fix.**

1. Derive the base URL at runtime rather than defaulting to a constant:
   - port from `hass.http.server_port` (cross-check `hass.config.api.port`)
   - scheme from `hass.config.api.use_ssl`
   - fall back to parsing `hass.config.internal_url`, then finally to
     `http://127.0.0.1:8123`
2. **Re-derive on reconnect**, not only at setup. The network settings page means
   the port can now change under a running instance, and the auto-rollback means
   it can change *twice* in five minutes.
3. Keep `CONF_RELAY_LOCAL_URL` as an explicit override and finally **expose it**
   — a field in the panel's remote-access tab. Derivation can be wrong behind
   unusual setups and there is currently no way for a user or for support to
   correct it.
4. Log the resolved URL once at relay start. Right now there is no way to tell
   from the outside what we're dialling.

**Tests.** [`hacs-addon/tests/test_relay_client.py`](../hacs-addon/tests/test_relay_client.py)
pins `127.0.0.1:8123` in roughly eight assertions (lines ~107, 123, 127, 191,
655, 657, 691, 706, 796, 800). Rather than mass-updating them, turn them into the
regression suite: parametrise over a fake `hass` reporting port 80, port 8123,
and SSL-on, and assert the dialled URL follows.

**Note:** the add-on panel is *not* affected — [`vome/panel/server.py`](../vome/panel/server.py)
talks to Core via `http://supervisor/core/api` with `SUPERVISOR_TOKEN`, which is
port-agnostic. Only the integration's loopback path is broken.

### A2. Device registry — one config entry per device (P1, deadline 2027.8)

**The change.** Devices are restricted to a single config entry and at most one
subentry. Physical devices that used to merge across integrations now appear once
per integration, and users get "replaced device" notices in Settings → Repairs to
re-point automations.

**Deprecated, that we use:**

| Deprecated | Replacement |
|---|---|
| `async_get_device(identifiers={...})` | `async_get_device_by_identifier((DOMAIN, id), entry_id)` |
| `DeviceEntry.config_entries` | `config_entry_id` |
| `DeviceEntry.primary_config_entry` | `config_entry_id` |
| `DeviceInfo(via_device=...)` | `via_device_id` (passing both raises `HomeAssistantError`) |
| `async_update_device(add_/remove_config_entry_id=...)` | `new_config_entry_id=` / `new_config_subentry_id=` |

**Our call sites (all use the deprecated identifier lookup):**

- [`switch.py:198`](../custom_components/vomesync/switch.py#L198) — rename sync
- [`coordinator.py:954`](../custom_components/vomesync/coordinator.py#L954) — device removal on forget
- [`config_flow.py:~2130`](../custom_components/vomesync/config_flow.py#L2130) — orphaned-device cleanup
  (this one already uses `dr.async_entries_for_config_entry`, which is fine)

`sensor.py:91` and `switch.py:182` build `DeviceInfo` with
`identifiers={(DOMAIN, uid)}` and no `via_device`, so they need no change.

**Nothing breaks in 2026.8** — there's a compatibility shim through **2027.8**
covering ~90% of cases. But we're an `integration_type: device` integration that
owns its own devices, all three call sites have an `entry_id` in easy reach, and
the migration is mechanical. Do it now while it's cheap, in the same release as
A1.

**Watch:** installs with duplicate linked Vome entries (a known real state — see
the relay-topology memory, gamlabio hit this) may generate "replaced device"
repair notices. Put a line in the release notes.

### A3. Trusted proxies (P2 — mostly docs)

Forward-UI relays request headers through with only hop-by-hop headers stripped
(`_filter_forward_headers`, `relay_client.py:144`), so whatever `X-Forwarded-For`
nginx set arrives at Core — from `127.0.0.1`.

Today this is harmless: HA ignores the header unless `use_x_forwarded_for` is
enabled. But 2026.8 promotes trusted proxies to a one-click setting on the new
Network page, so many more users will switch it on — and then `127.0.0.1` must be
in their trusted-proxy list or forwarded requests will 400.

**Do:** add a trusted-proxy check to the panel's `/api/diag` verdict card, and a
note in the Connect setup guide.

### A4. Approachability language pass (P3)

HA replaced ~43 instances of "advanced"/"expert" with plain feature descriptions,
and renamed **Developer Tools → Tools**. Our panel copy, [`docs/`](.) and the
portal guides use both the old menu name and exactly the vocabulary HA just
removed. Cheap credibility win; our guides read as stale against the new UI
otherwise.

### A5. Verification

Sandbox is on HA 2026.7.2. Bump it to 2026.8 and re-run the ingress panel drive
(supervisor `POST /store/reload` → `/store/addons/b1bff62e_vome/update` → HA
restart → ingress session + panel API with both `Cookie: ingress_session` and
Bearer token). **Specifically exercise a port-80 flip** to prove A1 — that's the
whole point of the exercise.

### Release packaging

A1 and A2 both touch the integration and want one coordinated bump (integration
0.9.13 / add-on 0.3.14). Remember: an add-on update does **not** reload the
integration — Core imports `custom_components` only at startup, so users must
restart HA before new code takes effect. Bundling A1+A2 avoids asking Connect
subscribers to restart twice.

---

## Part B — Nabu Casa Cloud vs Vome Connect

**Price context.** Cloud is **$6.50/mo (~62 kr)** or $65/yr. Connect is
**69 kr/mo** with the friendly domain (49) and backup plan (29) comped. We're at
rough price parity while bundling more infrastructure — but Cloud wins on
breadth, and breadth is what gets compared on a feature grid.

| Cloud feature | Vome Connect today | Verdict |
|---|---|---|
| Remote UI (per-instance DNS + TLS) | Friendly domain `*.home.vome.io`, comped | **Parity** |
| Offsite backups | We sell backup plans, but are **not registered as an HA backup agent** — we don't appear in HA's own backup UI | **Gap, small** |
| Alexa + Google Assistant | None | **Gap, large** |
| Cloud TTS / STT | None | **Gap, medium** |
| Webhooks (cloudhooks) | None as a product; forward proxy already carries the traffic | **Gap, small** |
| WebRTC / TURN for cameras | None | **Gap, medium** |
| Guided setup wizard | Panel is functional but not guided | **Gap, UX** |
| Scoped LAN-TCP tunnels (cameras, non-HTTP controllers) | Yes | **We win — Cloud has nothing** |
| CHAP standby / failover | Yes | **We win** |
| Health reports, uptime visibility | Yes | **We win** |

### The cheap wins, in order

**B1. Backup agent — best value per line of code we have available.**

Implement `custom_components/vomesync/backup.py`:

- `async_get_backup_agents(hass)` → list of agents
- `async_register_backup_agents_listener(hass, *, listener, **kwargs)` → unregister fn
- a `BackupAgent` subclass with `async_upload_backup`, `async_download_backup`,
  `async_list_backups`, `async_get_backup`, `async_delete_backup`, plus
  `domain` / `name` / `unique_id`; raise `BackupAgentError` on failure

We already *sell* offsite backups. This makes them a destination inside HA's own
backup screen instead of a portal-side thing the user has to remember exists —
which is precisely how Nabu Casa presents theirs. Server side needs an upload
endpoint with per-server auth and quota accounting against the backup plan.

**B2. Cloudhook equivalent.**

`https://<slug>.home.vome.io/webhook/<id>`, scoped to the webhook path only. The
relay already forwards HTTP, so this is mostly routing rules plus a token model
and a registration UI. Closes a checkbox on the comparison grid for near-zero
engineering.

**B3. TURN / STUN.**

We already run public infrastructure with a TLS terminator. A coturn instance
plus ICE-server registration would light up remote camera viewing — the single
most common "why is remote access slow" complaint. Confirm the current HA API
surface before starting: the camera WebRTC provider API (`CameraWebRTCProvider` /
`async_register_webrtc_provider`) has moved more than once, and there's a
separate path for supplying ICE servers without being a full provider. **Check
against 2026.8 source, not blog posts.**

**B4. Guided setup in the panel.**

HA just set the expectation with its Cloud redesign: a step-by-step wizard with
deferral, replacing a single page of toggles, and a dedicated page per feature
(remote access, backups, voice, companion app). Our panel has the right
information but presents it as a wall. This is where the comparison gets made
most viscerally, and it's design work rather than infrastructure.

---

## Part C — Voice: what it would actually take

**Decision for now: not building it.** This section is so the decision gets
revisited with facts rather than re-researched from scratch.

### The good news: the HA-side work is nearly zero

Home Assistant Core **already ships** the `alexa` and `google_assistant`
integrations, which implement the full smart-home message handlers
(`alexa.smart_home.async_handle_message`,
`google_assistant.smart_home.async_handle_message`) plus entity-to-trait mapping,
discovery, and state reporting.

Nabu Casa's `cloud` component does **not** reimplement any of that. It supplies
config objects (`CloudAlexaConfig`, `CloudGoogleConfig`) subclassing the same
`AbstractConfig` ABCs, hosts the skill/action endpoint, does OAuth account
linking, and proxies messages to the instance over the connection it already
has. We would write `VomeAlexaConfig` / `VomeGoogleConfig` against those same
ABCs and route over the relay we already have.

**So the barrier is not Home Assistant. It's Amazon and Google.**

### Amazon (Alexa Smart Home skill)

- Amazon developer account; create an **Alexa Smart Home skill**
- Smart Home skills require an **AWS Lambda ARN** endpoint — a plain HTTPS
  endpoint is not accepted for this skill type (confirm against current docs;
  this has been a stable constraint but is worth re-checking)
- **OAuth2 authorization-code account linking**, with the Vome portal acting as
  the authorization server — we'd need `/authorize` + `/token` endpoints, client
  registration with Amazon, and token refresh
- **Certification review** to publish. Until published, the skill is usable only
  by accounts you explicitly add as beta testers — fine for a pilot, blocking for
  GA
- Per-region availability has to be declared and tested

### Google (Home / smart home Action)

- Google Cloud project + Google Home Developer Console
- HTTPS **fulfilment URL** (no Lambda requirement here)
- OAuth2 account linking, same portal-as-auth-server work as Amazon
- **HomeGraph API** + **Report State** — we must push state changes proactively,
  which means a fan-out path from every linked instance. This is the piece with
  real ongoing infrastructure cost
- Certification review for public release

### Honest cost assessment

- **Engineering:** the HA-side config objects are days. The OAuth authorization
  server, the two cloud endpoints, Report State fan-out, and per-user linking
  state are weeks.
- **Calendar:** certification on both platforms is measured in weeks of review
  latency, not days, and rejections are common on first submission.
- **Ongoing:** platform API churn, re-certification, and support load for
  "Alexa can't find my device" — a category of ticket that is notoriously hard to
  debug remotely.
- **Runtime cost:** Lambda and HomeGraph calls are cheap. This is not a hosting
  cost problem; it's an engineering and maintenance problem.

### Recommended positioning instead

Don't compete on voice. Say plainly: *keep using Home Assistant Cloud for voice
if you want it — Connect does the things Cloud can't.* The LAN-TCP tunnel story
(cameras and non-HTTP controllers that can't run a mesh-VPN agent), scoped guest
access, CHAP standby and failover are genuinely differentiated, and none of them
have a Cloud equivalent. Voice is a commodity checkbox where we'd be the fourth-
best option; the tunnels are something only we do.

Revisit if either: (a) a meaningful number of Connect prospects cite voice as the
blocker, or (b) we're already building an OAuth authorization server in the
portal for another reason, which removes the largest single chunk of the work.

---

## Suggested order for tomorrow

1. ~~**A1** — derive the local core URL~~ ✅ done; panel override field still open
2. **A2** — three device-registry call sites
3. **A5** — sandbox to 2026.8, exercise a port-80 flip end to end
4. Ship as integration 0.9.13 / add-on 0.3.14, one HA restart
5. **A3 + A4** — diag check and copy pass
6. **B1** — backup agent (the standout cheap win)
7. **B2** — webhooks
