CLI Contract Matrix
===================

Small CLI, big promises. This table declares what each command touches, whether it needs a network, and which exit codes you can rely on. If it’s not here, it’s not a contract.

.. list-table::
	 :header-rows: 1

	 * - Command
		 - Purpose
		 - Schroot touches
		 - Local repo touches
		 - Offline support
		 - Network required
		 - Exit codes (stable)
		 - Stability
	 * - init
		 - Bootstrap config, caches, and ``openstack/releases``
		 - No (sets up metadata only)
		 - No
		 - N/A
		 - Yes unless already primed
		 - 0 success; nonzero on failure
		 - Stable
	 * - plan
		 - Resolve dependency graph without building
		 - Reads existing schroots (no mutation)
		 - Reads local repo (no mutation)
		 - Yes (``--offline``)
		 - No unless pulling fresh refs
		 - 0 ok; 1 usage; 5 missing pkgs; 6 dep cycle
		 - Stable
	 * - build
		 - Build package end-to-end and publish to repo
		 - Creates if missing (sbuild builds)
		 - Publishes artifacts, updates indexes
		 - Yes (``--offline`` disables PackaStack fetches)
		 - Yes unless fully pre-seeded
		 - 0 ok; 1 config; 2 tools; 3 fetch; 4 patch; 5 missing; 6 cycle; 7 build; 8 policy; 9 registry
		 - Stable
	 * - refresh
		 - Fetch/validate archive indexes with TTL/ETag
		 - No
		 - No
		 - Yes (``--offline`` validates cache presence)
		 - Yes for fresh fetch; no when using cached indexes
		 - 0 ok; 1 usage; 2 partial online failure; 3 offline missing; 4 corrupt cache
		 - Stable
	 * - clean
		 - Remove cached tarballs/extractions, workspaces, or local repo
		 - No
		 - Yes (can delete local repo)
		 - N/A
		 - No
		 - 0 ok; nonzero on failure
		 - Stable

Notes
-----
- Offline support means PackaStack avoids its own network fetches and requires pre-seeded caches; it does not firewall schroot network access.
- Stability refers to intent: options may grow, but existing behaviors and exit codes remain unless the contract table is updated.
- Missing CLI verbs? They’re not implemented. Roadmap ideas (auto-init, schroot/admin helpers) will appear here only when real.
- ``build`` upgrades ``debian/watch`` to version 5, prefers ``gbp dch`` for changelog entries (opt-out available), exports patch queues, and returns you to ``master`` after patch handling.
