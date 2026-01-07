Dependency Resolution (How PackaStack Decides What Else to Build)
=================================================================

You asked to build one thing; PackaStack decides which friends it must invite to keep the party upright. This page walks through how dependencies are mapped, planned, and—if you allow—auto-built.

Naming and mapping
------------------
PackaStack first figures out what you meant. It peeks into your local repo; if a matching source tree exists, it uses that. Otherwise it asks ``openstack/releases`` how to spell the thing (libraries get the ``python-<name>`` cape). From there it draws a plan graph using archive metadata plus anything you’ve already built.

What gets built alongside
-------------------------
Build-deps are the only tagalongs PackaStack will auto-build, and only if ``--build-deps`` stays on (default). Missing build-deps get built first and published to your local repo so the main build can lean on them. Runtime deps are assumed to live in the archive or your repo—you own them if you want different versions. Cycles or gaps are shouted about during planning so you don’t waste sbuild minutes.

Upstream version floors are enforced by default. Use ``--min-version-policy report`` to keep older archive versions while still flagging them as "outdated", or ``--min-version-policy ignore`` to treat them as satisfied. Either way, the build writes a dependency satisfaction report (text + JSON) alongside other run artifacts when ``--dep-report`` is left on.

Packaging type propagation
--------------------------
Release vs snapshot vs milestone is a promise you make for the target only. Dependencies keep whatever versions the archive or your local repo provide unless you explicitly rebuild them. Flipping ``--snapshot`` does not cascade; PackaStack prefers stability for the rest of the graph.

Offline considerations
----------------------
With ``--offline``, PackaStack stares only at cached indexes and your local repo. Missing build-deps trigger loud failures; nothing is fetched. Run online first to pre-seed indexes and any build-deps you expect to need.

.. admonition:: Really sucks right now — but we’ll polish it
   :class: warning

   There’s no per-package override to “force snapshots for all deps” yet. If you need that, you must rebuild those deps yourself and let the local repo serve them. A smarter cascade toggle is on the wish list.

Debugging resolution
--------------------
``packastack plan`` is your flashlight: ``--pretty`` for a legible tree, ``--plan-upload`` for ordering. If a dep is missing, check whether it’s actually in ``<workspace>/localrepo`` and whether pinning prefers it; if not, build it first. When in doubt, rerun planning with ``--offline`` to make sure you’re not secretly leaning on the public archive.
