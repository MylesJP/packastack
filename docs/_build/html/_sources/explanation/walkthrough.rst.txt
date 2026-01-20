End-to-End Walkthrough (Gory Edition)
=====================================

This is the full tour of a PackaStack run—from the moment you type ``packastack init`` until artifacts land in the local repo and offline builds purr. It explains what happens, why it happens that way, and where the bodies are buried. PackaStack is polite, but it’s not shy about showing you the plumbing, including the squeaky valves.

0) Before you start: what PackaStack assumes
--------------------------------------------
You’re on an Ubuntu host with ``schroot``, ``sbuild``, and ``git`` installed, you have a writable workspace (``~/packastack-work`` is fine) with enough disk to feed hungry schroots, and you’re happy to let PackaStack babysit schroots and the local repo.

1) init: set the stage
----------------------
``packastack init`` writes ``~/.config/packastack/config.yaml`` if you don’t already have one, lays out caches under ``~/.cache/packastack`` (runs, ubuntu-archive), clones or updates ``openstack/releases``, and sprinkles metadata breadcrumbs so later steps know which Ubuntu devel series to chase.

.. admonition:: Really sucks right now — but we’ll polish it
   :class: warning

   There’s no ``packastack auto-init`` yet. Forgetting to run ``init`` means your first ``build`` will nag you. Future UX might prompt or run it for you, but today you must type it.

2) refresh: trust but verify the archives
-----------------------------------------
Refresh fetches (or merely validates) the Packages.gz indexes for release/updates/security across main/universe and the arches you care about. TTLs are honored via ETag/If-Modified-Since; ``--force`` elbows its way past the cache, while ``--offline`` just checks whether the pantry is stocked. Each index gets metadata so later commands know fresh from stale.

.. admonition:: Really sucks right now — but we’ll polish it
   :class: warning

   Mirror overrides and corporate proxies can still bite. If your proxy isn’t set in the environment, refresh will fail loudly. We plan smoother proxy hints; for now, export your proxy before running.

3) plan: shape the dependency graph
-----------------------------------
Planning resolves your target into source/binary names via ``openstack/releases`` and whatever already lives in your local repo. It sketches the dependency graph, yells if there’s a cycle, and writes a run summary. ``--offline`` keeps it strictly pantry-only; ``--skip-local`` pretends the pantry doesn’t exist if you want a clean-room view.

Why it works this way: planning before building catches missing packages and cycles early, saving schroot time and sbuild pain.

4) build: orchestration in motion
---------------------------------
Build time: PackaStack checks that ``packastack-<series>-<arch>`` exists, creating or refreshing as needed. It preps sources (release tarballs or snapshots), applies policy, resolves build-deps (and can build them if you allowed ``--build-deps``), then drives the builder—``sbuild`` by default, ``dpkg-buildpackage`` if you insist—inside the schroot. Artifacts, indexes, and logs land under ``<workspace>/output`` and get published into ``<workspace>/localrepo``. With ``--offline``, the schroot’s network card is metaphorically yanked; pre-seeding is mandatory.

What actually runs: ``sbuild -d <series> --arch <arch> -c packastack-<series>-<arch> <foo>.dsc`` plus a handful of ``--chroot-setup-commands`` that bind-mount the local repo into ``/srv/packastack-apt`` and add it to APT sources, followed by matching cleanup commands. Switch to ``--builder dpkg`` and PackaStack swaps sbuild for ``dpkg-buildpackage``—useful for source-only spins but less hermetic.

How to read the build output: the run log dir (``<workspace>/output/<run>/logs``) holds ``sbuild.stdout.log`` and ``sbuild.stderr.log``—the full orchestral score. ``sbuild-artifacts.json`` is the liner notes: command, exit code, where artifacts were collected from, and what made the cut. A primary-log symlink points you to the crown-jewel log; start there. Fresh ``.deb``, ``.changes``, and ``.buildinfo`` files should also appear in ``<workspace>/localrepo``. If they don’t, believe the exit code, not your hopes.

.. admonition:: Really sucks right now — but we’ll polish it
   :class: warning

   There is no first-class ``packastack schroot`` admin command yet. If a schroot is haunted, you delete it with system tools and rerun ``build`` to recreate. A helper CLI is on the roadmap.

5) tests and validations
------------------------
PackaStack runs tests unless you tell it otherwise; “boring green” is the default mood. Logs and artifacts stay under ``output``, and the schroot pins your local repo highest so you’re testing what you just built, not something the internet gifted you.

6) offline mode reality check
-----------------------------
``--offline`` flips the schroot network to “absolutely not.” DNS, apt, curl, git—none shall pass. If the build still completes, your pre-seeding is legit. If it doesn’t, go online, fill the gaps, and try again.

7) Cleanup and idempotence
--------------------------
Schroots are recreated as needed, the local repo is rewritten per build, and you can delete outputs or schroots on a whim—PackaStack will regenerate them. If you nuke the workspace (after backups), rerun ``init`` and it will happily rebuild your world.

Why the design leans this way
-----------------------------
- Determinism beats cleverness: fixed schroot names, documented bind mounts, and pinned local repos keep runs reproducible.
- Offline-first mindset: if it can work offline, it should; if it can’t, the failure should be loud and early.
- Small CLI surface: fewer verbs, clearer contracts; everything else lives in config and conventions.

If you want the short, happy-path version, see :doc:`../tutorial/quickstart`. For the exact switches and exit codes, see :doc:`../reference/cli` and :doc:`../reference/cli-contract`. For specific recipes, head to :doc:`../howto/index`.
For deeper dives, see :doc:`building-packages` for the sbuild/DPKG path and :doc:`dependency-resolution` for how PackaStack decides what else to build alongside your target.
