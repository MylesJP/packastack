Design Trade-offs
=================

PackaStack picks predictability over clever stunts. If something must give, speed yields to determinism. We’d rather rebuild a schroot than nurse along a flaky one. Pinning, TTLs, and offline fences keep behavior steady; caches are there to help, not to hide problems.

The CLI stays small on purpose. You get four verbs—init, plan, build, refresh—until we can ship more without creating mystery states. Missing knobs (like a schroot subcommand) are intentional pauses, not oversights.

Contracts beat vibes. What’s documented is guaranteed; everything else is fair game to change. Upgrades stay safe when you live inside the contract.

Offline is binary here. If you say ``--offline``, downloads are off the table. Missing resources cause loud failures with pointers to refresh or rerun online. No surprise “I fetched it for you” moments.

Outputs are boring by design. Artifacts, logs, and summaries always land in known paths. At 2 a.m. you can script against them without guessing where they went.
