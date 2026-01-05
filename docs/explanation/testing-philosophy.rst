Testing Philosophy
==================

PackaStack wants failures early, loud, and well-documented. “It probably works” is not a test plan; “here’s the log and the provenance” is.

By default, tests are on and offline rules apply to them just like builds. If you claim air-gapped, we enforce it. When something goes red, the logs and run summaries are expected to tell you why—mysteries belong in novels, not CI dashboards.

Inside the schroot, we build with sbuild or dpkg in the same environment you’ll ship from. Policy checks ride along: snapshot eligibility, changelog and version sanity, watch file expectations. Provenance is captured so you know which upstream source, hash, and signature went into the artifacts.

Results surface in structured run summaries with exit codes and step markers. Artifacts and logs land in ``<workspace>/output`` and the local repo; breadcrumbs, not shrugs.

Honesty about gaps: there isn’t a standalone ``packastack test`` verb yet—tests hitch a ride with builds. If a dedicated runner appears, it will join the CLI reference. Policy coverage will grow, but the promise of loud failures and clear logs stays.

Practical rhythm: rerun the build to rerun tests and keep the same options for apples-to-apples comparisons. When debugging, refresh or rebuild one schroot to rule out environment drift before chasing ghosts.
