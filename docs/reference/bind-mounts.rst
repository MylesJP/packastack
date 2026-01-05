Bind Mounts Contract
=====================

PackaStack uses bind mounts to make specific host directories available inside schroots. This enables reproducible builds, efficient caching, and safe artifact extraction. The list of bind mounts is fixed and documented; if you find a writable mount that isn’t documented, please report it (and consider buying a lottery ticket).

+-----------------------------------------------+---------------------------+-------------------+-----------------------------------+
| Path inside schroot                           | Host path                 | Access            | Purpose                           |
+===============================================+===========================+===================+===================================+
| /srv/packastack-apt                            | paths.local_apt_repo       | read-only         | Local APT repo for build deps     |
+-----------------------------------------------+---------------------------+-------------------+-----------------------------------+
| /etc/apt/sources.list.d/packastack-local.list | generated                 | read-only         | Local repo source list            |
+-----------------------------------------------+---------------------------+-------------------+-----------------------------------+

Only the documented mounts are present. Any deviation is a bug. The local repo mount is read-only; modifying it from inside the schroot is unsupported.

Do not modify ``/etc/apt/sources.list.d/packastack-local.list`` inside the schroot. It is generated per build.

Contractual Guarantees
----------------------
- The list of bind mounts is fixed and documented.
- Only the documented paths are mounted; all others are read-only or not mounted.
- Users must not modify bind-mounted paths outside the documented writable set.
- Bind mount configuration is invariant across builds for a given workspace.

See also: :doc:`schroot`, :doc:`local-repo`, :doc:`offline-mode`, :doc:`../overview/invariants`
