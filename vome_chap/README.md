# Vome CHAP

Companion to the **Vome** add-on for homes protected by Vome CHAP: a copy of
your Home Assistant that Vome keeps ready to take over if this machine fails.

It does three things, only once this install has been paired from the Vome
portal (Standby sync):

- **Keeps the standby in step.** Your configuration is sent whenever it
  changes; on the standby it is written in while Home Assistant there is
  stopped.
- **Follows a failover.** While the standby runs your home, Home Assistant
  here is stopped, so the two never act at once; it starts again when you
  hand back, after taking any changes made while it was away.
- **Fills the standby the first time**, with a one-off backup of your add-ons
  and folders, made under a throwaway key. The standby's own Vome CHAP fetches it and restores your
  add-ons and folders from it, but not Home Assistant itself, so the standby
  never starts up as a second copy of your home. Your configuration arrives
  by sync. The backup and its key are deleted on both sides and at Vome
  afterwards.

It needs the Supervisor's *manager* permission to stop and start Home
Assistant and to make that backup — which is why it is a separate add-on:
the Vome add-on on its own never asks for it.
