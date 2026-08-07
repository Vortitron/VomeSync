# Plan: Home Assistant 2026.8 compatibility + Connect vs Nabu Casa Cloud

Written 2026-08-06, updated 2026-08-07. **Part A is complete, green in tests, and
the port fix is verified live on a real HA 2026.8** — but nothing is released.
Read the A5 corrections: the original blast-radius claim was overstated, and a
second live instance of the same bug was found in the portal. Part B is still a
to-do list.

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

**Panel override — ✅ DONE 2026-08-07.** New `vomesync.set_local_url` service
(response-ONLY, guarded, rejects bare hosts and paths; blank clears the
override), panel route `/api/local_url`, and an editor in the panel's
Home Assistant UI view showing the address in use and whether it was detected,
overridden, or guessed. The Overview grows a warning card when detection fell
back to a guess.

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

### A2. Device registry — ✅ DONE 2026-08-07

**Implemented:** new `custom_components/vomesync/device_compat.py` with
`async_get_device_by_identifier()` / `async_remove_device_by_identifier()`.
Both migrated call sites now scope by `config_entry_id`. The helper picks the
scoped 2026.8 API when the registry exposes it and falls back to the old global
lookup otherwise — a **capability check, not a version comparison**, because
`hacs.json` still supports HA back to 2024.1 where the new helper does not
exist. 8 new tests cover both branches plus the unscoped and missing-device
cases. No `via_device`, `primary_config_entry` or `add_/remove_config_entry_id`
usage was found, so nothing else needed touching.

The original analysis follows.

#### Original analysis

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

### A3. Trusted proxies — ✅ DONE 2026-08-07

**Implemented:** `_trusted_proxy_check()` in `services_remote.py` compares the
address we dial against `hass.http.trusted_proxies` and reports
`{ok, hint}` in the status payload; the panel renders a warning card when
`ok is False`. `ok is None` means "can't tell" (a hostname target we cannot
resolve) and deliberately produces no warning — crying wolf here would train
people to ignore the card. Note `use_x_forwarded_for` is not stored on
`hass.http`, so a non-empty trusted-proxy list is taken as the signal that
filtering is on.

The original analysis follows.

#### Original analysis

Forward-UI relays request headers through with only hop-by-hop headers stripped
(`_filter_forward_headers`, `relay_client.py:144`), so whatever `X-Forwarded-For`
nginx set arrives at Core — from `127.0.0.1`.

Today this is harmless: HA ignores the header unless `use_x_forwarded_for` is
enabled. But 2026.8 promotes trusted proxies to a one-click setting on the new
Network page, so many more users will switch it on — and then `127.0.0.1` must be
in their trusted-proxy list or forwarded requests will 400.

**Do:** add a trusted-proxy check to the panel's `/api/diag` verdict card, and a
note in the Connect setup guide.

### A4. Approachability language pass — ✅ DONE 2026-08-07

Renamed Developer Tools → **Tools** (and Services → **Actions**) across
`ENTITY_MANAGEMENT.md`, `TEST_ENTITY_LINKING.md` and `hacs-addon/README.md`.
Dropped "Advanced:" from the relay-server option label and title, and replaced
its "only change this if you know what you're doing" with a description of what
the setting is for. Internal identifiers (`CONF_SWITCH_ADVANCED`,
`async_step_create_switch_advanced`) were left alone — HA's change was about
labels users read, and renaming option keys would migrate stored config for no
user-visible gain.

The original analysis follows.

#### Original analysis

HA replaced ~43 instances of "advanced"/"expert" with plain feature descriptions,
and renamed **Developer Tools → Tools**. Our panel copy, [`docs/`](.) and the
portal guides use both the old menu name and exactly the vocabulary HA just
removed. Cheap credibility win; our guides read as stale against the new UI
otherwise.

### A5. Verification — ✅ DONE 2026-08-07 (live on real 2026.8)

Ran on the PLC sandbox (`4139120f-…`, Primo VM 10.100.11.130), upgraded
2026.4.3 → **2026.8.0**, driven from the HAOS serial console via
`/root/hacon.py` (a pexpect wrapper left on Primo — reusable).

**Result: the fix works.** With integration 0.9.13 deployed and Core bound to
port 80, `get_remote_status` returned
`local_url: "http://127.0.0.1:80"`, `local_url_source: "detected"`. Flipping
Core back to 8123 and restarting returned `http://127.0.0.1:8123`. Derivation
tracks the real port in both directions on a real 2026.8 instance.

That call also exercised the **unlinked** path (`linked: false`), which is how I
found and fixed a gap in my own change: `get_remote_status`'s unlinked
early-return did not include the new fields, so the panel's forwarding view
would have shown a blank address on any not-yet-linked install.

#### Corrections to the original analysis — read these

1. **"Silent, total loss of remote access" was too strong.** HA 2026.8 keeps a
   **compatibility listener on port 8123** (same process) that answers
   `307 Temporary Redirect` with the path preserved
   (`Location: http://127.0.0.1/api/states`). Any client that follows redirects
   — including `aiohttp` by default, i.e. our REST dispatch — keeps working.
   The fix is still right, but the pre-fix blast radius on HAOS is **smaller
   than stated**. What genuinely breaks without it: clients that do *not*
   follow redirects (see 2), WebSocket bridging (`ws_connect` redirect handling
   is not something to rely on), and the local-TLS case, which never worked.
2. **A second instance of the same bug exists in the portal, and it is live.**
   `/var/www/konhas.com/portal/` hardcodes 8123 in ~19 places
   (`ha_core_api.py:26 HA_PORT`, `ha_ws_command.py:37 HA_PORT`,
   `supervisor_api.py`, `ha_backdoor.py`, `container_ops.py`,
   `admin_server_routes.py`, …). Proven broken during this session: the portal's
   own token refresh does
   `curl -sS ... http://<vm_ip>:8123/auth/token` with **no `-L`**, so it
   received the literal body `307: Temporary Redirect` instead of a token and
   reported "refresh failed". The same request against `:80` returned a valid
   token. For hosted VMs the portal *assigns* the port so this is latent — but
   any customer who flips their own HA to port 80 via the new Network page
   takes portal VM access down with them. **Not fixed; needs its own pass.**
3. **HTTP settings moved to `/config/.storage/http`** with
   `"yaml_migration_done": true`. `configuration.yaml`'s `http:` block is
   migrated once and then **ignored** — editing it has no effect, which cost
   time here and will confuse support. The store also holds a `pending` slot
   used by the confirm/rollback flow.
4. **The five-minute auto-rollback is real and fires.** `ha core options --port
   80` showed `port: 80` immediately, then reverted to `8123` on its own
   because nothing confirmed it. Worth knowing before debugging a "my port
   change didn't stick" report.
5. **The MCP broker reported failure on an operation that succeeded.**
   `POST /core/update` returned a broker 500, but the update completed. Do not
   treat broker errors as proof the operation failed — check the actual state.

#### Not verified live

- **Re-derivation without a restart.** Covered by
  `test_port_change_is_picked_up_without_a_restart`, but not proven on real
  hardware: changing the port through the storage file needs a Core restart
  anyway, and the relay was not linked on this rebuilt sandbox so
  `set_local_url` (which needs a linked entry) could not be driven either.
- **Relay dispatch end to end.** The sandbox VM has been rebuilt and is no
  longer linked to Vome, so nothing exercised the actual tunnel. Derivation is
  proven; the dispatch path that consumes it is not.
- ~~**A2 device-registry changes** were deployed but not exercised.~~
  **✅ Exercised live 2026-08-07** — see below.

#### A2 exercised live (2026-08-07)

Both migrated call sites driven against the sandbox on HA 2026.8, integration
0.9.13:

* **Removal** (`coordinator.py`): `create_switch` → device
  `a2-registry-check` appeared in the registry; `forget_switch` → device gone.
  The proof is tight because the caller wraps removal in `try/except` — a wrong
  signature would have been swallowed and the device would have *survived*.
* **Rename** (`switch.py`): driven through the options flow over REST
  (init → manage_switches → select → edit_switch → submit). Device
  `0ef1ec96…` changed name in the registry and was renamed back afterwards.
* **The scoped branch is the one that ran**, not the legacy fallback:
  `hasattr(DeviceRegistry, 'async_get_device_by_identifier')` is `True` on
  2026.8, so `device_compat` took the new API.
* Log clean — no `device_registry` deprecation warnings, no swallowed
  exceptions, no "Could not remove device".

Sandbox restored (switch name back to `claude-e2e-test-2`, port 8123).

#### Separately noticed

HA 2026.8 / Python 3.14 flagged `websocket_client.py:117` for **blocking SSL
calls inside the event loop** (`load_default_certs`, `set_default_verify_paths`
via `websockets.connect`). **Fixed 2026-08-07** (commit `6484a0b`): pass
`homeassistant.util.ssl.get_default_context()`, which HA builds once at import
time, off the loop. `None` for plaintext `ws://`.

#### Original plan

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

## Part A′ — Portal `:8123` pass (different repo: `/var/www/konhas.com`)

✅ **Core modules done 2026-08-07.** Found during A5; see A5 correction 2 for how.

**The design turns on one measured fact.** `curl -L` is *not* a fix for the 8123
compatibility redirect: curl **drops the `Authorization` header across a
port-only redirect**. Verified locally with a two-port redirect harness — same
host, 307, `AUTH=<<ABSENT>>` on both GET and POST (the *method* survives, the
credential does not). So following redirects would convert every authenticated
portal call from a 307 into a 401. The port has to be discovered.

**New `portal/ha_endpoint.py`.** `resolve_ha_port(cs, vm_ip)` does one cheap
unauthenticated probe of `:8123` and adopts the redirect's port; `ha_base_url()`
wraps it. Properties worth keeping:

- **Same-host only.** A redirect to another host is refused — following one
  would send tenant traffic somewhere we did not intend.
- **Port allowlist** (80/443/8123/8124), so a misconfigured or compromised guest
  cannot aim us at an arbitrary service.
- **Never fatal.** Any probe failure or exception falls back to 8123; discovery
  makes calls *more* likely to land, it can never be why one fails.
- **Cached in-process** (15 min TTL) with `invalidate()`. No schema change for
  something that moves about once a year.

**Probe target matters.** It probes `/api/`, not `/`. Measured against the live
sandbox: `/` answers `302 → /onboarding.html` whether or not anything moved, so
a redirect there proves nothing; `/api/` answers a flat `401` when the port is
right and only redirects when it genuinely moved.

**Verified live, both directions.** With the sandbox on 8123 the resolver
returned `http://10.100.11.130:8123`. With it moved to 80, the raw probe from
the container server returned `307 http://10.100.11.130/api/` and the resolver
returned `http://10.100.11.130:80` — confirming HA emits a `Location` the
parser accepts when probed across the network, not just from localhost. (That
was worth checking: if the header had carried a configured base URL instead,
the parser would have refused it and silently fallen back to 8123, keeping the
bug while looking fine.)

21 unit tests in `tests/test_ha_endpoint.py`.

**One bug found by the portal's own suite.** The cache key did
`(cs or {}).get('id')` *outside* the guarded block, and several call paths pass
`cs` as a bare string — so a non-dict raised `AttributeError` straight through
the "never fatal" contract. Now via `_cs_id()`, with a parametrised test over
every `cs` shape the real call paths use.

**Wired through:** `supervisor_api.py` (all six sites — token refresh, the two
WS-helper argv calls, `/api/config`, `/api/hassio{endpoint}`, backup upload,
core logs), `ha_core_api.py` (the brokered-HA engine), `ha_ws_command.py`.
Fixing the refresh alone would have been worse than useless — the token would
renew and every subsequent call would still hit the wrong port.

**Still hardcoded, deliberately not touched:** `admin_server_routes.py`,
`container_ops.py`, `custom_domain_service.py`, `nginx_maps.py`, `ha_proxy.py`,
`database.py`, `ha_backdoor.py`. Most of these are the **host-side port
allocation scheme** (`8123 + offset`), which is the portal's own choice of
where to publish a VM and is *not* the same bug — changing it blindly would
break provisioning. `ha_backdoor.py` and the `container_ops.py` health probes
are genuine instances and should be a follow-up.

**Not deployed.** Portal ships main→live via Jenkins; nothing pushed.

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

**Scoping note (2026-08-07): smaller than first estimated.** The portal already
has the whole storage pipeline — `portal/backup_plans.py` (`stage_offsite_copy`,
`process_backup_arrival`, `latest_backup_info`, `_prune_local_generations`),
`backup_destinations.fan_out_backup`, Fernet encryption under
`BACKUP_ENCRYPTION_KEY`, local generations plus per-destination GFS rotation.
Today it is fed from `chap_service.native_backup_path(server_id)` — backups
*arriving* from a hosted/CHAP VM.

So B1 is **not** "build backup storage". It is two smaller pieces:
1. an authenticated **upload endpoint** that drops a file where
   `stage_offsite_copy` already expects it, entitlement-checked against the
   Connect backup plan; and
2. the HA-side `backup.py` `BackupAgent` that streams to it.

Everything downstream — encryption, rotation, fan-out, retention — is done.
Worth re-checking `ENCRYPT_MAX_BYTES` (512 MB) before shipping: larger backups
stage *unencrypted* with only a log warning, which is a defensible choice for
our own VMs but a different proposition for a self-hoster's data we invited in.

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

**B2. Cloudhook equivalent — ✅ DONE 2026-08-07 (bar an end-to-end test).**

**Half of it already existed, and it was the permissive half.** The portal
(`friendly_domains.get_forward_settings` / `forward_policy_for_host`, behind a
`cookieless_feature_enabled()` kill switch) and the webserver
(`uiProxy.cookielessAccess`) already admitted `/api/webhook/…` without the
login cookie when a per-server `webhooks` flag was set. What was missing was
the component end: the relay refused those requests unless `forward_ui` was on,
so the path dead-ended.

That means flipping the portal flag would have exposed **every** webhook on the
instance — including ones an integration creates later that the owner never
sees. The new `webhooks.py` allowlist is what makes the flag safe: the portal
decides *whether* webhook paths skip the login, the component decides *which
ids* are actually forwarded. Neither layer is sufficient alone.

Shipped: `webhooks.py` (exact-match only — extra segments, traversals and
percent-encoded ids are refused rather than normalised, because the thing being
matched is a secret), `set_webhooks`/`add_webhook`/`remove_webhook`, a Webhooks
panel view, 52 unit tests plus relay-level tests pinning that `/api/states`,
`/api/config`, `/lovelace` and `/auth/token` stay refused with webhooks on.

**Still open:** no end-to-end test with a real linked HA on 0.9.14, and worth
confirming the portal exposes `set_forward_settings` in its own UI — the flag
has to be on for any of this to work.

**Note the neighbouring `open` flag** in the same policy removes the cookie gate
*entirely*. Pre-existing, much broader than webhooks, worth knowing it is there.

The original plan follows.

**B2 (original).**

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

1. ~~**A1** — derive the local core URL + panel override~~ ✅ done
2. ~~**A2** — device-registry call sites~~ ✅ done
3. ~~**A3 + A4** — trusted-proxy check and copy pass~~ ✅ done
4. ~~Version bump to integration 0.9.13 / add-on 0.3.14~~ ✅ done (not released)
5. ~~**A5** — sandbox to 2026.8, port-80 flip~~ ✅ done, fix verified live
6. **Portal `:8123` pass** — see A5 correction 2; live bug, own repo ← **next**
7. **B1** — backup agent (the standout cheap win)
8. **B2** — webhooks

**Release state:** integration 0.9.13 / add-on 0.3.14, suite green (324 passed,
1 skipped), derivation verified live on HA 2026.8 in both port configurations.
Still **not committed, pushed or released** — that is a deliberate hold, not an
oversight.
