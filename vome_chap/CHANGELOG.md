# Changelog

## 0.2.14 — 6 November 2026

**A standby's apps are kept up to date by each other.** We found that a
standby never updated this app after all. Home Assistant's Supervisor does
not let an app update itself, so every try since 0.2.7 was refused, and its
automatic updates are carried out by Home Assistant, which a standby keeps
stopped. GamlaBio.local was still on 0.2.8 a week after 0.2.13. Now the Vome
app (0.3.81 and later) updates this one while Home Assistant is stopped, if
automatic updates are on for it, and this app updates the Vome app to the
version your main install runs, as it already did for your other apps. This
app no longer tries to update itself. If Vome sees a standby on an older
Vome CHAP than your main install, the CHAP page and an email say so, with the
command to update it by hand.

## 0.2.13 — 4 November 2026

**A local pair works beside a Vome pair.** If your main install was in a
local pair and in a Vome CHAP pair at once, the local pair switched itself
off. Then, when Vome moved your home to its hosted standby and stopped the
main install's Home Assistant, the local standby could no longer see the main
install and took over too, with the same configuration: your home ran in two
places besides the hosted one. Now the local pair keeps running. Where Vome
says the main install must not run your home, its Home Assistant stays
stopped, and it tells the local standby where your home is running, so the
standby stands by and says why. If the main install's machine dies outright,
the local standby still stands in. When the main install comes back after a
stand-in, the local pair brings the home back to it the usual way, and Vome
waits for that rather than start a second Home Assistant.

**Starting Home Assistant stops after a while.** If Home Assistant would not
start, the local pair asked the Supervisor again every ten seconds, for as
long as it took. When the Supervisor's own start had hung, that kept every
other Home Assistant job on the machine waiting until it was restarted. This
app now first checks whether Home Assistant is in fact up, and counts that as
started. Otherwise it tries again less and less often, and after half an hour
it stops and says so: start Home Assistant by hand, or restart the machine.
It notices if you start it yourself, and tries again when your home moves or
when this app restarts.

## 0.2.12 — 4 November 2026

**A standby restarts into the operating system update it took.** Asked to
update its operating system, a standby could have the update installed and
then left waiting for a restart that never came, so it stayed on the old
version. It now restarts into it, and does the same for an update it finds
installed and waiting.

## 0.2.11 — 4 November 2026

**A standby keeps up with your main install's updates.** A standby never took
add-on or operating system updates while it stood by, because its Home
Assistant is stopped and nobody opens it. So when it took over, it came up
months behind, with a pile of updates waiting. This app now tells Vome which
versions this install runs, and a standby takes the updates your main install
has taken, when Vome asks: each add-on only to the exact version the main
runs, never past it, and an add-on that was stopped stays stopped. The
operating system goes last, since it restarts the machine. When an update
cannot be done (the app store does not offer that version yet, say), Vome
hears why and shows it on the CHAP page.

## 0.2.10 — 31 October 2026

**Vome hears when a disk is filling up.** Each version of Home Assistant a
standby follows leaves the previous one's image behind, about 3.4 GB each,
because a standby's Home Assistant never starts and so is never cleaned up.
This app cannot remove images. So it now tells Vome how much space the
Supervisor counts as free, at most every ten minutes. When a standby has less
than 4 GB left, so that the next update would not fit, Vome warns you on the
CHAP page and by email, and says what to remove.

## 0.2.9 — 31 October 2026

**An app you uninstall stays gone.** After the install running your home
comes back from a failover, this app starts the apps it stopped there. One
you had uninstalled in the meantime was still on its list, so it asked for it
to start every five minutes, forever. It now lets go of any app that is no
longer installed.

## 0.2.8 — 30 October 2026

**A standby keeps its Supervisor up to date too.** It turned out the store was
not the only thing a standby never refreshed. Its Supervisor never looked for
a newer version of itself either, so once the main install's Supervisor moved
on, the standby refused every copy of your add-ons' data that the newer one
made. This app now has the Supervisor look for updates every six hours,
updates the Supervisor first when a newer one is out, and then updates itself.

**A refused copy says why.** When the Supervisor refuses to restore your
add-ons' data, Vome now gets its own words, not just "HTTP 400".

## 0.2.7 — 29 October 2026

**A standby keeps itself up to date.** Automatic updates were on, but on some
boxes Home Assistant's app store never refreshes by itself, so it never heard
that a newer Vome CHAP was out and the standby kept its old version until
someone ran `ha store reload` on it. Nobody opens a standby, so this app now
refreshes the store every six hours and, when a newer Vome CHAP is waiting,
updates itself. This applies on both installs and in both kinds of pair.
From 0.2.7 on, no one has to do it by hand.

*Correction, 6 November 2026:* the update of this app itself never worked.
The Supervisor does not let an app update itself. See 0.2.14.

## 0.2.6 — 28 October 2026

**When your main install can't send its changes, Vome hears why.** A
configuration bigger than the size Vome took was skipped on every pass, with a
line in this app's own log and nothing else, so Vome went on showing the
standby in step while it fell behind. Every check-in now carries what the
configuration adds up to and, if it could not be sent, the reason; Vome
counts the standby as behind from then, tells you, and does not copy the old
settings back after a failover.

**Bigger homes fit.** The limit is 512 MB packed (it was 256 MB), source maps
(`.js.map`, `.css.map`) are left out, and folders you choose on Vome's CHAP
page (a camera's snapshots, say) are left out both ways: never sent, and never
removed from the install that receives.

## 0.2.5 — 20 October 2026

**Your home's end-to-end key stays in your home.** A home with end-to-end
remote access keeps the private key for its own name under `.storage`, and a
snapshot took that directory with the rest, so a standby through Vome handed
Vome the one key that access exists to keep from it. The directory is no
longer copied, in either kind of pair; a standby that takes over issues a
certificate of its own.

## 0.2.4 — 7 October 2026

**An app you stop on the install running your home stays stopped.** The apps
your standby runs were started again on the running install every hour, when
their data was copied: stop Matter Server there on purpose and it came back.
Now they are started only when an install takes your home over (and kept
trying until they are up), or when you add one to the standby's list. If one
on the list is stopped where your home runs, Vome tells you and offers to take
it off the list or start it again; this app starts it when you say so.

## 0.2.3 — 5 October 2026

**Vome knows which version of this app each install runs.** If your standby
stops taking your changes because its Home Assistant is behind, Vome's
warning now says whether this app can catch it up by itself (it can, from
0.1.18) or needs updating first, instead of only "update Core first".

## 0.2.2 — 1 October 2026

**Your apps' own data follows too** -- a Zigbee2MQTT database, a Matter
fabric. The install running your home backs its apps up every hour, and
again whenever the home moves; the standby restores them with its Home
Assistant stopped (only the apps you chose for it, never Home Assistant
itself) and keeps them stopped until it takes over. The backup is protected
with the pair's own key and travels only between the two. The media folder
is left out, so a small standby is not filled with recordings. A move waits
for the apps' data as well as the configuration.

**Your home's address follows the running install, in a local pair too.**
Set *home_address* on the main install (an unused address on your house
network, e.g. 192.168.1.15) and point the app and your dashboards at it:
whichever Home Assistant runs your home holds it, so nothing needs changing
after a takeover. The standby learns it from the main install. Both installs
need a fixed address of their own. An address in use (an install's own, the
router's) or off your house network is refused, with a note in the log.

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

Quick or careful: a new *takeover_after* setting on the standby, how long
the main install must be out of reach (with your router answering) before
it takes over: 30 seconds to 15 minutes, 2 minutes as before if unset.

Clearer names: the two installs are "Main install" and "Standby" in their
notifications and on the app's page, which also shows each one's address;
or name them yourself with the new *install_name* setting ("Kitchen NUC").
Renaming does not re-pair.

Also fixed: in a local pair this app now turns on its own automatic updates,
as it does in a Vome pair. A standby's Home Assistant is stopped, so nobody
can open it to update the app; until now it kept the version it was paired
with. Pairs made with 0.2.0: update the app on the standby once by hand
(start its Home Assistant, update, and it stops again by itself).

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
