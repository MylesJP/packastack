Building Packages (Under the Hood)
==================================

This is the play-by-play of how PackaStack turns a source tree into binaries, which spells it chants, and how to read the tea leaves afterward. sbuild does the heavy lifting, PackaStack wires the local repo into the schroot, and you either get deterministic artifacts or a loud, useful failure.

From plan to .dsc
-----------------
PackaStack applies patches (via gbp), shapes the source tree, and emits a ``.dsc`` plus tarballs. That ``.dsc`` is the contract the builder obeys. Build mode decides whether binaries are part of the deal (default: yes) and which tool swings the hammer (default: ``sbuild``; flip ``--builder dpkg`` for source-only or quick local spins).

The sbuild command line
-----------------------
By default you’ll see something like ``sbuild -d <series> --arch <arch> -c packastack-<series>-<arch> <foo>.dsc``. PackaStack sprinkles in ``--chroot-setup-commands`` to bind-mount your local repo at ``/srv/packastack-apt`` and add it as a trusted source, plus matching cleanup commands so we don’t leave dishes in the sink. Any extra args you pass get stapled on; exit code 0 is applause, anything else is a frown.

What happens inside the schroot
-------------------------------
APT updates against the mounted local repo, so anything you’ve already built is first in line. Build-deps are resolved from that repo plus the Ubuntu archive view; with ``--build-deps`` on (default), missing build-deps get built earlier and are waiting on the shelf. sbuild then runs the usual Debian playbook: unpack, build, run hooks/tests, package, sign/annotate.

How to read the output
----------------------
In ``<workspace>/output/<run>/logs`` you’ll find ``sbuild.stdout.log`` and ``sbuild.stderr.log``—start with stdout for the plot, dip into stderr for the drama. ``sbuild-artifacts.json`` is the neatly typed postmortem: command, exit code, where artifacts were found, every file we copied. A primary-log symlink points at the crown-jewel log. Artifacts themselves land under ``<workspace>/output/<run>/`` and get mirrored into ``<workspace>/localrepo`` with fresh metadata. Expect ``.deb``, ``.changes``, ``.buildinfo``, and logs; no ``.deb`` plus a nonzero exit code means we call it a failure.

.. admonition:: Really sucks right now — but we’ll polish it
   :class: warning

   There’s no pretty HTML dashboard for build logs yet. You get structured JSON and logs; bring your own viewer. A richer “build report” page is on the wishlist.

DPKG path (when you choose it)
------------------------------
Flip ``--builder dpkg`` to skip schroot orchestration and run ``dpkg-buildpackage`` directly—nice for source-only or fast local checks. The bind-mounted local repo magic is sbuild-only; with dpkg you’re on your own for dependency resolution, so choose wisely in CI.

Troubleshooting reads
---------------------
Nonzero exit code with no ``.deb``? Open ``sbuild.stderr.log`` and search for “E:” or “FATAL”. Missing build-deps? Check they’re actually in ``<workspace>/localrepo``; if not, rerun with ``--build-deps`` and make sure the plan includes them. Offline failures? Any network attempt in the logs means you’re missing a pre-seeded index or tarball—rerun online, then try again offline.
