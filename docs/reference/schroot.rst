Build Chroot Environment Contract
=================================

PackaStack uses an isolated chroot environment for sbuild-based binary builds. Two sbuild backends are supported, selected by ``sbuild.chroot_mode`` in ``config.yaml`` (default ``auto``; see :doc:`config`):

- **schroot**: A lightweight, chroot-based container managed by ``schroot`` (sbuild's default before 0.88). The schroot alias is ``packastack-<series>-<arch>`` (e.g., ``packastack-noble-amd64``). PackaStack creates a missing schroot using ``sbuild-createchroot`` (falling back to ``mmdebstrap``), which requires sudo.
- **unshare**: A rootless tarball chroot in ``~/.cache/sbuild/<series>-<arch>.tar.zst`` (sbuild's default since 0.88). PackaStack creates a missing tarball with ``mmdebstrap --mode=unshare``; no sudo is needed, but the host requires ``mmdebstrap`` and subordinate-id entries in ``/etc/subuid``/``/etc/subgid``.

The backends are incompatible with each other: an environment created for one cannot be used by the other. In ``auto`` mode PackaStack prefers whichever environment already exists (unshare tarball first, then a PackaStack schroot) and otherwise follows the installed sbuild's own default. Manual changes to either environment are not supported by the contract. Offline builds require the environment to already exist.

There are two environment flavors:

- **Distribution chroots**: Created from the official Ubuntu base images for a given series and architecture (supported).
- **Cloud archive chroots**: Not implemented yet; planned for future releases.

If creation fails, PackaStack will emit a clear error and halt the build. Common causes include missing tools (``schroot``/``sbuild-createchroot``/``mmdebstrap``), missing subordinate-id entries (unshare), network issues (in online mode), or insufficient disk space. PackaStack does not automatically refresh build chroots; delete the schroot with system tools (or remove the tarball from ``~/.cache/sbuild/``) and rerun a build to recreate it.

For manual inspection, see :doc:`../howto/inspect-state`.

Contractual Guarantees
----------------------
- Schroot aliases are deterministic and documented for sbuild builds.
- PackaStack creates missing schroots on demand when online.
- Schroot environments are isolated except for documented bind mounts.

See also: :doc:`bind-mounts`, :doc:`local-repo`, :doc:`offline-mode`, :doc:`../overview/invariants`
