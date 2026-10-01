# Vome CHAP

Companion to the **Vome** add-on for homes protected by Vome CHAP: a copy of
your Home Assistant that Vome keeps ready to take over if this machine fails.

It does four things, only once this install has been paired from the Vome
portal (Standby sync):

- **Keeps the standby in step.** Your configuration is sent whenever it
  changes; on the standby it is written in while Home Assistant there is
  stopped.
- **Follows a failover.** While the standby runs your home, Home Assistant
  here is stopped, so the two never act at once; it starts again when you
  hand back, after taking any changes made while it was away. The install
  that takes over says so in a Home Assistant notification ("You are on
  …"), so you can tell which one you are looking at.
- **Keeps the standby's add-ons in step.** The add-ons you choose for the
  standby (ESPHome, say) are sent across whenever your home moves and every
  hour, so a Matter device paired on one install is on the other too.
- **Fills the standby the first time**, with a one-off backup of your add-ons
  and folders, made under a throwaway key. The standby's own Vome CHAP fetches it and restores your
  add-ons and folders from it, but not Home Assistant itself, so the standby
  never starts up as a second copy of your home. Your configuration arrives
  by sync. The backup and its key are deleted on both sides and at Vome
  afterwards.

It needs the Supervisor's *manager* permission to stop and start Home
Assistant and to make that backup — which is why it is a separate add-on:
the Vome add-on on its own never asks for it.

## Two Home Assistants in your house, no Vome needed

The same add-on can pair two Home Assistants in your house with each other,
so the second takes over when the first stops -- also when your internet is
down. No Vome account, no time limit. Step by step, with pictures:
<https://vome.io/chap/local>.

1. Install **Vome CHAP** on both Home Assistants.
2. On the one running your home, set **local_pair** to `main` in the add-on's
   *Configuration* tab and save. The pairing code is on the add-on's own page
   (*Open Web UI*, or *Show in sidebar*), which only administrators can open;
   a notification tells you it is ready.
3. On the other, set **local_pair** to `standby`, paste the code into
   **pair_code**, and save.

The standby stops its Home Assistant and the home's add-ons, takes a copy of
the main install's configuration, and keeps taking it as it changes (over
your house network, encrypted with the key in the code: nothing else can
connect or read it).

**When it takes over.** If the standby can reach your router but not the
main install -- neither this add-on nor its Home Assistant -- for two
minutes (or whatever its *takeover_after* setting says: shorter is quicker,
longer rides out a reboot), it starts its Home Assistant and your add-ons
and says so in a notification. A standby that cannot see the router either does nothing: it
is the one cut off.

**When the main install comes back** it sees the home has moved, stops its
own Home Assistant and takes the standby's configuration. Once it has been
back and in step for three minutes, your home moves back to it by itself:
Home Assistant stops on the standby for a minute or two while the last
changes go across, so nothing is lost.

**Moving it yourself.** To work on the main install, open the *Vome CHAP*
panel on it and press *Move the home to …*. The home stays on the standby
until you move it back the same way.

**A smaller standby.** The standby can be a lesser machine: on the main
install's *Vome CHAP* panel, tick which of the standby's add-ons it runs
when it stands in. The rest stay stopped there.

**Good to know**

- Made the standby by restoring a backup of the main install? Pair it
  straight away: until then both run as the same home and take turns on
  your Vome address. Pairing stops the standby's Home Assistant.
- Set **home_address** on the main install (an unused address, e.g.
  192.168.1.15) and point the app at it: whichever install runs your home
  holds it.
- Give both machines fixed addresses on your network (a reservation on your
  router), so they can always find each other.
- A USB radio (a Zigbee or Z-Wave stick) cannot move between machines. Use a
  network coordinator (Ethernet Zigbee, Thread border router) for devices you
  want to keep working after a takeover.
- The running install never stops itself for losing sight of the others: if
  your router dies, your home keeps running. If only its cable comes out, the
  standby takes over too, and when it is plugged back in, the main install
  stops; anything changed on it in between is lost.
- Your add-ons' own data (a Zigbee2MQTT database, say) is copied too, every
  hour and whenever the home moves (from 0.2.2; not the media folder).
  Install the same add-ons on both.
- SSH, File editor, Studio Code Server and Samba keep running on the standby,
  so you can still reach it.
- If you also use CHAP through Vome, leave local_pair `off`: Vome's pair
  decides, and the two would disagree.

## Licence

Releases from 29 September 2026 (version 0.1.17 on) are under the Functional
Source License (FSL-1.1-MIT): free to use, change and share, at home, with or without Vome;
not for a competing commercial product or service. Each release becomes MIT
two years after it is published. See [LICENSE.md](../LICENSE.md).
