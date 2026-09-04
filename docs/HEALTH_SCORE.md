# The health score, before there is an account

*Integration side. The Vome side is `docs/guest_score_plan.md` in the
portal repo.*

Vome's health check reads a Home Assistant and writes up what it finds —
noisy sensors, a swelling database, dead entities, backups that stopped
happening — and scores it out of 100. It used to be reachable only after
signing up on vome.io and linking an instance, which is the wrong order:
the check is the thing worth having, and it was behind two steps that
each gave people a reason to leave.

So the button is here, and it works with no account.

## What happens when you press it

`vomesync.health_score_run` (Developer Tools → Actions, or a dashboard
button) does one of two things:

**This instance is not linked to Vome.** It calls
`POST /api/v1/relay/guest`, which opens a throwaway account at Vome's
end, provisions the relay link, queues the check, and answers with the
credentials plus one URL. The tunnel comes up, the check runs against
this instance, and a notification appears with that URL. Nothing is
typed and nobody signs up.

**This instance is already linked.** It just asks for a check on the
account that owns it (`POST /api/sync/agent/health-check`).

Either way the finished report is pulled back here
(`GET /api/sync/agent/health-report`, authenticated by the relay secret
this instance already holds) and published on
`sensor.vome_health_score`: the score is the state, the findings,
summary and categories are attributes.

## Which install you need

Either. The check needs the integration and nothing else: the relay
mints its own local access token through `hass.auth` and never touches
the Supervisor (that is only used to find an ESPHome dashboard), so a
Container or Core install with HACS runs the same check and gets the
same report on the same sensor.

What the add-on adds is the button. On a HACS-only install the same
thing is *Developer tools → Actions → Vome: Check my health score*,
which is a fine answer for somebody who already lives in there and a
poor first impression for anybody else — so vome.io/score/try leads
with the add-on and keeps HACS as a link underneath.

## The two-hour clock, and why it is said out loud

A guest run is temporary. Vome deletes the check, the findings and the
link to this Home Assistant two hours after it starts, unless somebody
opens the URL and signs in.

That deal only works if it is visible, so it is stated in three places:
the notification when the run starts, the notification when the score
lands, and the sensor itself (`saved_to_account: false`, `keep_it_url`,
`deleted_in_seconds`). A temporary link must never be presented as a
finished one — `TestTheClockIsHonest` in
`hacs-addon/tests/test_health_score.py` exists to keep that true.

Three ways it can end, all handled here:

* **Claimed.** Vome re-points the same server at the real account, so the
  credentials keep working — the guest flags come off and it becomes an
  ordinary link with no reconnection and no re-linking. Vome says so via
  `"guest": false` on the report response rather than us inferring it.
* **Expired.** The credentials stop working. Rather than leave a dead
  link looking alive, the integration drops it, stops the tunnel and says
  so once. The last report stays on the sensor.
* **Discarded** from the web page. Same as expired, from this side.

## Why the report lives here

Vome computes it — the collectors and the AI write-up are its, and the
write-up needs a key this integration does not have — but the answer is
about *this* system. Keeping it on an entity means a dashboard card, an
automation or a person can read it without a round trip to a website,
and it is what makes the guest deal fair: when Vome deletes its copy,
the house still has one.

## Services

| Service | What it does |
|---|---|
| `vomesync.health_score_run` | Runs a check (linking first if needed). Returns immediately; the score arrives on the sensor and in a notification. `use_ai: false` asks for a locally-written summary instead. |
| `vomesync.health_score_get` | The last report as data, refreshing from Vome first unless `refresh: false`. |

## What is sent

Only the findings leave the instance, and only to be turned into prose —
never states, history, configuration or backups, and nothing at all if
`use_ai: false`. Vome derives its disclosure from the code that sends
it: <https://vome.io/privacy#ai>.
