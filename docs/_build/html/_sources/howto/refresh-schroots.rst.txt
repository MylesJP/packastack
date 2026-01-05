How to Refresh Schroots
=======================

Schroots age like bread, not wine. PackaStack recreates and refreshes schroots automatically during builds, but there’s no dedicated `packastack schroot` command yet. Here’s how to get a clean schroot anyway.

When to Refresh
---------------
- After a new Ubuntu release or cloud archive update
- When a schroot feels corrupted or misconfigured
- When a build fails in ways that smell like environment drift

Force a fresh schroot (current workaround)
-------------------------------------------
1. List existing schroots (for sanity):

   .. code-block:: bash

      schroot --list | grep packastack || true

2. Remove the suspect schroot using your system schroot tooling (delete its definition under ``/etc/schroot/chroot.d`` and its root under ``/var/lib/schroot/chroots`` if present). Be cautious and back up first.

3. Re-run your PackaStack build:

   .. code-block:: bash

      packastack build nova --series noble --arch amd64

   PackaStack will recreate the schroot on demand.

Notes
-----
- This is temporary; a `packastack schroot` CLI is planned.
- If you find a schroot that refuses to refresh or recreate, file a bug (and consider a career in ghostbusting).
- See also: :doc:`../reference/cli` for current commands and :doc:`../reference/cli-contract` for stability promises.


