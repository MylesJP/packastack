Local Repository Contract
=========================

The local APT repository is PackaStack’s staging area for all built packages. It ensures that only the packages you build are used in subsequent test and install steps, providing a reproducible and isolated workflow. The local repo is not a dumping ground for old packages—cleanup is enforced, and pinning is strict.

The local repo is created automatically in the workspace (typically ``<workspace>/localrepo``). It contains standard Debian repository structure: ``pool/``, ``dists/``, ``Packages``, and ``Sources`` files. Only packages built by the current PackaStack run are published here; external packages are not imported.

Package flow is as follows: Packages are built in schroot and copied to the local repo. The repo is indexed and updated after each build. Tests and install steps use only packages from the local repo, unless explicitly overridden. The local repo is pinned with the highest priority in the schroot’s APT configuration, ensuring your built packages are always preferred over Ubuntu or external sources.

Old or superseded packages are removed from the local repo after each build. The repo is invalidated and rebuilt if the workspace is cleaned or if a schroot is refreshed. Manual modification of the local repo is not supported and may result in undefined behavior (and possibly stern warnings).

If the repo cannot be updated (e.g., disk full, permission denied), PackaStack will halt and report the error. If a required package is missing, tests will fail with a clear diagnostic.

Contractual Guarantees
----------------------
- The local repo is created at a documented path within the workspace.
- All built packages are published to the local repo before any test or install step.
- The repo is cleaned up according to the documented policy.
- Pinning ensures the local repo takes precedence during tests.

See also: :doc:`schroot`, :doc:`bind-mounts`, :doc:`offline-mode`, :doc:`../overview/invariants`
