How to Safely Clean Up
======================

Workspaces collect old schroots, stale packages, and other detritus. PackaStack will eventually grow dedicated cleanup commands, but today you need a light touch with system tools.

Cleaning the Local Repo
-----------------------

There is no ``packastack repo`` command yet. To reset the local repo, delete its contents; PackaStack repopulates on the next build:

.. code-block:: bash

   rm -rf ~/.cache/packastack/apt-repo/*

If you keep a curated subset, remove only the package directories you no longer need.

Destroying Schroots
-------------------

PackaStack manages schroots automatically but does not expose a schroot subcommand yet. To forcefully remove one:

1. List them: ``schroot --list | grep packastack``
2. Remove the matching definition under ``/etc/schroot/chroot.d`` and the root under ``/var/lib/schroot/chroots`` (back up first).
3. Run a build; PackaStack will recreate the schroot as needed.

Full Workspace Reset
--------------------

If you need to start from scratch, remove the entire workspace (after backing up anything important):

.. code-block:: bash

   rm -rf <workspace>

Warning: This is irreversible. If you delete your workspace, you get to keep both pieces.

Best Practices
--------------
- Prefer targeted deletion (specific schroot or package) over wiping everything.
- If you’re unsure, back up your workspace first.
- After a full cleanup, rerun ``packastack init --prime`` before the next build.
- If cleanup fails or leaves things inconsistent, file a bug with logs and details.

See also: :doc:`../reference/cli` for current commands and :doc:`../reference/cli-contract` for the exit-code contract.
