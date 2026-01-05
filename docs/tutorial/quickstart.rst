PackaStack Quickstart
=====================

This single tutorial takes you from zero to a proven, repeatable build with the minimum number of yak encounters. You’ll prep the workspace, let PackaStack herd schroots and the local repo, run a build, and then brag that it works even with the Wi-Fi unplugged. Mild wizardry, no manual yak-shaving required.

What you need
-------------
- An Ubuntu host with `schroot`, `sbuild`, and `git` on board.
- Network access for the first lap (we’ll pull the plug later).
- A workspace directory, e.g., ``~/packastack-work``—any tidy corner will do.

1) Initialize once
------------------
Set up configuration, caches, and the OpenStack releases checkout. One-liner, once per workspace:

.. code-block:: bash

   packastack init

Optional: tack on ``--prime`` to slurp Ubuntu archive metadata immediately (it internally calls ``packastack refresh``).

2) Let PackaStack manage schroots (no manual commands)
-------------------------------------------------------------
Schroots appear and refresh on demand. There is no `packastack schroot` button (yet); just run the build and PackaStack conjures the right one. If a schroot feels haunted, delete it with system schroot tools and rerun the build—PackaStack will summon a fresh one. Dedicated CLI is planned; for now, enjoy the hands-free magic. Quick peek, just to feel in control:

.. code-block:: bash

   schroot --list | grep packastack || true

3) Build (hands free)
---------------------
Kick off a build. PackaStack orchestrates schroot prep, dependency resolution, compilation, and publishing to the local repo. You provide the package name; PackaStack provides the orchestra.

.. code-block:: bash

   packastack build nova --series noble --arch amd64

Behind the scenes it quietly refreshes or creates the schroot, prepares sources, resolves dependencies, stashes artifacts and logs in ``<workspace>/output``, and publishes packages into ``<workspace>/localrepo``.

4) Inspect artifacts and repo
-----------------------------
Take a victory lap and peek at the outputs:

.. code-block:: bash

   tree <workspace>/output
   tree <workspace>/localrepo

5) Prove offline readiness
--------------------------
After at least one successful online build (to warm caches and the local repo), go air-gapped: disable Wi-Fi or unplug, then run:

.. code-block:: bash

   packastack build nova --offline --series noble --arch amd64

If it succeeds, you’re fully pre-seeded. If it fails, reconnect, rebuild without ``--offline`` to fill gaps, or run ``packastack refresh`` to update metadata. The goal is boring offline success—boring is good, smug is better.

What’s next
-----------
- Need targeted recipes? See :doc:`../howto/index`.
- Want guarantees and contracts? See :doc:`../reference/index`.
- Curious about design rationale? See :doc:`../explanation/index`.
