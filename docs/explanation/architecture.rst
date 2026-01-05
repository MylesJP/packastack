Architecture Overview
=====================

PackaStack is a politely opinionated assembly: a thin CLI on top of a runner that choreographs schroots, a local repo, and a cache that remembers what happened. The vibe is “boringly reliable,” not “mystery automation.” Every run leaves footprints you can audit.

Picture a command entering the system. Typer turns your keystrokes into intent. RunContext opens the curtain, wiring in logging, spinners, summaries, and exit codes. Paths decide where everything lives: cache, repo, schroots, run logs. If the expected schroot is missing or stale, it gets built or refreshed before work begins. Planning resolves dependencies; building drives sbuild or dpkg with the chosen modes. Outputs and metadata land in stable directories—nothing evaporates into ``/tmp`` and disappears.

Under the hood, ``packastack.core`` keeps configuration and run plumbing tidy while ``packastack.commands`` exposes only four verbs (init, plan, build, refresh). The build layer steers modes and provenance; the apt helpers fetch and curate archives and the local repo; upstream and debpkg code know where tarballs and policy come from; planning draws the dependency graph so you don’t build in circles.

Contracts are explicit: schroot names are deterministic and recreated on demand; local repo and cache paths are fixed by config so artifacts and summaries always land where you expect; offline fences actually block network inside schroots when you ask for ``--offline``; exit codes match the CLI reference so CI can depend on them.

Day to day, the system stays idempotent where it should: rerun init or refresh without fear. Everything is observable because runs write summaries and logs, and repos and outputs sit in predictable paths. Downloads happen only in refresh and build, and they announce themselves in the logs—no surprises.
