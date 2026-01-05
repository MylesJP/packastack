Schroot Environment Contract
=============================

A schroot is a lightweight, chroot-based container used by PackaStack to provide a deterministic, isolated build environment for Ubuntu packages. Schroots are managed by PackaStack for sbuild-based binary builds; manual changes are not supported by the contract.

Schroots are created on demand for a given Ubuntu series and architecture. The schroot alias is ``packastack-<series>-<arch>`` (e.g., ``packastack-noble-amd64``). PackaStack creates the schroot using ``sbuild-createchroot`` when it is missing. Offline builds require the schroot to already exist.

There are two schroot modes:

- **Distribution schroots**: Created from the official Ubuntu base images for a given series and architecture (supported).
- **Cloud archive schroots**: Not implemented yet; planned for future releases.

If schroot creation fails, PackaStack will emit a clear error and halt the build. Common causes include missing tools (``schroot``/``sbuild-createchroot``), network issues (in online mode), or insufficient disk space. PackaStack does not automatically refresh schroots; delete a schroot with system tools and rerun a build to recreate it.

For manual inspection, see :doc:`../howto/inspect-state`.

Contractual Guarantees
----------------------
- Schroot aliases are deterministic and documented for sbuild builds.
- PackaStack creates missing schroots on demand when online.
- Schroot environments are isolated except for documented bind mounts.

See also: :doc:`bind-mounts`, :doc:`local-repo`, :doc:`offline-mode`, :doc:`../overview/invariants`
