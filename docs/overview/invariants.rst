Invariants and Guarantees
=========================

PackaStack’s contract is explicit: what is documented here is guaranteed. Anything not documented is not guaranteed and may change without notice. If you rely on undocumented behavior, you’re speedrunning “living dangerously” with the difficulty set to comedy. This page summarizes the core invariants and guarantees that underpin all PackaStack workflows.

- Schroot names and lifecycle are deterministic and documented (no mystery pets).
- The local APT repository is always created, cleaned, and pinned according to policy.
- Bind mounts are fixed and documented; only documented paths are writable (if it’s not on the list, it stays read-only).
- Offline mode means no network access, ever, during build or test. No, really.
- Tests are always run unless explicitly disabled; surprises belong in birthday parties, not CI logs.
- The CLI contract table is the authoritative source for command behavior and stability.

If you observe behavior not covered here, file a documentation bug. If you rely on undocumented behavior, you do so at your own risk (and possibly your own amusement).
