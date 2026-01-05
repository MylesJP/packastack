How to Inspect Schroot and Repo State
=====================================

Knowing what your schroots and local repo look like right now is the fastest way to avoid wild guesses. PackaStack gives you simple probes so you can inspect and verify without superstition.

Inspecting Schroots
-------------------
- List all schroots (a quick headcount before you start poking around):

  .. code-block:: bash

     schroot --list

- Enter a schroot for manual inspection (look, but try not to touch too much):

  .. code-block:: bash

     sudo schroot -c packastack-noble-amd64 -u root -- bash

- Check schroot configuration files (usually in ``/etc/schroot/chroot.d/``) to confirm mounts and names match expectations.

Inspecting the Local Repo
-------------------------
- List all packages in the local repo (the “what have we built lately?” view):

  .. code-block:: bash

     tree <workspace>/localrepo/pool

- Check repo metadata (is your package actually published?):

  .. code-block:: bash

     cat <workspace>/localrepo/dists/*/Packages

- Verify pinning and APT configuration (highest priority should point to your local repo):

  .. code-block:: bash

     grep -r Pin-Priority <workspace>/localrepo/apt.conf.d/

If you find something unexpected, refresh or clean before rerunning builds. If you find something truly mysterious, file a bug (and maybe a short story). Plot twists welcome.

See also: :doc:`../reference/cli` for command flags and :doc:`../reference/cli-contract` for the stability matrix.
