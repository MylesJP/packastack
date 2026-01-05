Reproducibility Model
=====================

PackaStack treats reproducibility like a promise: same inputs, same outputs, same exit codes. If a result shifts, it should be because you changed an input—or because we owe you a bug fix.

Inputs are loud. Ubuntu series, OpenStack target, builder choice, release versus snapshot: all of these live on the CLI and in the logs. Sources come from ``openstack/releases`` plus your local repo; there are no shadow mirrors. Schroot names stay deterministic (``packastack-<series>-<arch>``) so you always know which room the build took place in.

State is captured, not whispered. Run summaries record the series, options, exit codes, and which steps completed. Artifacts and repo contents sit in stable paths. Refresh metadata (ETag and Last-Modified) rides alongside Packages.gz so you can prove what you fetched and when.

Network boundaries are real fences. Online fetches happen in explicit phases such as refresh or an online build. Offline mode slams the door inside schroots; missing pieces trigger clear failures instead of polite, hidden downloads.

Tools behave deterministically. sbuild and dpkg run with predictable flags; changelog and version handling follow gbp-friendly conventions. Dependency graphs come from Packages indexes rather than ad-hoc guessing.

Some variables still move: upstream releases and archive contents evolve, and host toolchain versions (sbuild, git, dpkg-dev) matter. Pin snapshots of ``openstack/releases`` and Packages.gz when you need to freeze time, and keep CI and dev hosts aligned.

Practical habit: refresh before critical runs or pin what you depend on; keep run summaries and repo snapshots when promoting builds. Future-you will be grateful.
