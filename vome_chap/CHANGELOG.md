# Changelog

## 0.2.1 — 1 October 2026

Security, from a review of the local pair:

- The pairing code is no longer in a Home Assistant notification, which
  every user of Home Assistant can see; it is on this app's page, which only
  administrators can open. The notification says where to find it.
- Once paired, the two installs switch to a key of their own, derived for
  that pair: an old pairing code -- in a screenshot, say -- opens nothing
  but an introduction, and a second install with the code cannot replace
  your standby. Pairs made with 0.2.0 carry on while they update.
- Limits on what the other install can send: a configuration snapshot of
  at most 256 MB that unpacks to at most 1 GB (this also guards snapshots
  through Vome), and small answers otherwise; at most 16 connections at once.
- The app page's form token is compared in constant time.

## 0.2.0 — 29 September 2026

**The local pair: two Home Assistants in your house, no Vome needed.**
Step by step, with pictures: <https://vome.io/chap/local>

- Pair two installs from this app's settings: `local_pair: main` on the one
  running your home shows a pairing code; paste it into the other's
  `pair_code` with `local_pair: standby`.
- The two talk only to each other, on port 8177 of your house network, over
  TLS keyed by the pairing code: nothing else can connect or read it.
- The standby keeps a copy of the main install's configuration, taken over
  the house network with its Home Assistant stopped, following the main
  install's Home Assistant version first.
- When it can reach your router but not the main install for two minutes, it
  takes over: its Home Assistant and your apps start, and it says so in a
  notification. A standby that cannot see the router either does nothing.
- The main install never stops itself for losing sight of the others, so a
  dead router leaves your home running.
- When the main install is back and in step for three minutes, your home
  moves back to it, the careful way: nothing changed on the standby is lost.
- The app's own page (Open Web UI, or Show in sidebar): the pair, the pairing
  code, "Move the home to …", and which apps a smaller standby runs when it
  stands in.
- An install connected to Vome but in no Vome pair can pair locally; your
  Vome address follows whichever install runs your home.
- Licence: FSL-1.1-MIT, as the repository has been since 29 September.

## 0.1.18 — 29 September 2026

- A stopped standby keeps itself up to date: it updates its own Home
  Assistant to the running install's version, and turns on automatic updates
  for this app.

## 0.1.17 — 29 September 2026

- Reports the port Home Assistant listens on (not always 8123), so the other
  install of a house pair checks it in the right place.

## 0.1.0 – 0.1.16 — 24–26 September 2026

- 0.1.16: holds your home's address at home while running your home; sets
  full-UI forwarding when asked.
- 0.1.15: reports its apps and addresses when Vome asks, and hourly.
- 0.1.14: reports each network interface's gateway.
- 0.1.13: says in Home Assistant which install is running your home.
- 0.1.12: reports this install's addresses with its apps.
- 0.1.11: sends the apps' data alone when Vome asks for a refresh.
- 0.1.10: starting apps can no longer hold up the check with Vome.
- 0.1.9: names a linked install after its Home Assistant; keeps why a seed
  failed.
- 0.1.8: reports whether the link to a hosted home is up, not only the home.
- 0.1.7: keeps this install's own entries for the integrations Vome names.
- 0.1.6: seeds only the apps that are running.
- 0.1.3 – 0.1.5: a seeded standby's apps stay stopped until it runs your
  home, on both sides, from the same pass as the restore.
- 0.1.2: the seed is a backup of your apps and folders, not Home Assistant.
- 0.1.1: the standby restores the seed itself, without starting Home
  Assistant.
- 0.1.0: CHAP moves into its own app, Vome CHAP, with the one-off seed that
  fills a new standby.
