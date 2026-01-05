Common Failure Signatures
=========================

A small field guide to the loud noises PackaStack can make, plus the usual fix.

- Clone of ``openstack/releases`` fails (network errors): rerun online; if proxy is involved, set it in your shell before ``packastack init``.
- Missing package when building offline: reconnect, rerun without ``--offline`` to populate caches and local repo, then retry offline.
- APT pinning ignored inside schroot: check ``/etc/apt/preferences.d`` in the schroot and ensure local repo pin priority is highest; rebuild the schroot if it drifted.
- Schroot missing or corrupted: delete with system schroot tools and rerun the build; PackaStack will recreate cleanly.
- Disk full while updating local repo: free space, remove stale artifacts in ``<workspace>/output`` or old packages in ``<workspace>/localrepo``, then rerun the build.
- Tests skipped unexpectedly: confirm you didn’t pass a skip flag; check logs for test harness errors; rerun with verbose logging if needed.
- Unexpected network access in offline mode: file a bug immediately (and enjoy the fireworks); in the meantime, double-check that you’re truly running with ``--offline`` and that caches exist.

If you hit something not on this list, jot down the error and file an issue. Mystery sounds belong in horror movies, not build logs.
