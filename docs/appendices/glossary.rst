Glossary
========

Because words mean things, and we like ours to be crisp.

- PackaStack: Canonical’s OpenStack packaging conductor; it cues schroots, local repo, and tests.
- Schroot: A chroot managed by ``schroot``; PackaStack names them ``packastack-<series>-<arch>`` and uses them as sterile build rooms.
- Local repo: The private APT repository in your workspace where PackaStack publishes built packages; pinned above everything else during tests.
- Bind mount: A host path mirrored into the schroot; PackaStack documents which are read-only and which can be written.
- Offline mode: A build/test run with networking blocked inside the schroot; only pre-seeded resources are allowed to play.
- Prime: The ``--prime`` option on ``packastack init`` that forces immediate archive metadata fetches.
- TTL: The cache timeout controlling when archive metadata is refreshed; ``--prime`` sets it to zero for a fresh pull.
- Invariant: A behavior PackaStack promises will not change without notice (and would update the docs if it did).
- Haunted schroot: A schroot that behaves strangely due to manual edits or disk glitches; the cure is deletion and recreation.
- Boring success: The ideal outcome of a build—nothing surprising happened, everything was logged, and you can go make tea.
