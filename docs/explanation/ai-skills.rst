AI Skills and build-doctor
==========================

PackaStack's AI-assisted triage system is called **build-doctor**.  It looks at a failed sbuild, picks the right specialist "skill" for the failure shape, and runs it.  The design goal is that the Python runtime stays stable while the skill catalog evolves — adding a new handler for a new class of failure is a folder drop, not a source-code change.

This document explains *why* the system is structured that way.  For configuration, see :doc:`../reference/config`.  For a walkthrough of adding your own specialist, see :doc:`../howto/add-an-ai-skill`.

Design in one sentence
----------------------

**Skills are data on disk; the runtime is a deterministic dispatcher.**  Python owns control flow, policy, and validation.  The LLM classifies failures and produces structured output against a typed contract.  Skills never call other skills directly — the router returns a recommendation and Python decides whether to honour it.

Architecture
------------

Four layers:

1. **Core runner** (``packastack/ai/runner.py``) — loads skills, assembles context, calls the model, parses the response against a contract.
2. **Router skill** (``build-doctor``) — a skill whose job is to pick the next skill.  Returns a structured :class:`DispatchPayload`.
3. **Specialist skills** — focused playbooks for one class of failure each (patch refresh, python compat, library sync, etc.).
4. **Context collectors** (``packastack/ai/collectors.py``) — named, reusable callables (``failure_header``, ``sbuild_log_tail``, ``working_tree``, ``ai_memory``) that each skill requests via its frontmatter.

Skill format
------------

Every skill is a folder with a single ``SKILL.md``:

.. code-block:: yaml

   ---
   name: python-compat
   description: Patch upstream source to fix breakage caused by a newer Python interpreter
   version: 1
   output_contract: patch
   when_to_use: Use when a build failure is caused by a Python version bump removing or renaming stdlib APIs…
   requires_context:
     - failure_header
     - sbuild_log_tail
     - working_tree
     - ai_memory
   triggers:
     log_patterns:
       - "ModuleNotFoundError: No module named 'distutils'"
       - "module 'ast' has no attribute 'Str'"
   ---
   You are a Debian packaging expert…  (prompt body)

Four knobs, each with a purpose:

- ``output_contract`` — one of ``patch``, ``diagnosis``, ``dispatch``, ``guidance``.  Tells the runner how to parse the model's response into a typed payload.
- ``requires_context`` — which collectors to run and prepend to the prompt.  The runner assembles these in order.
- ``triggers.log_patterns`` — regexes used for cheap pre-routing.  When a match hits, the specialist is chosen without calling the router at all.
- ``when_to_use`` — human-readable hint.  Injected into the router's skills menu so the router can match failures to skills.

Both ``name`` and ``description`` are required; everything else is optional with sensible defaults.

End-to-end flow
---------------

When ``diagnose_build_failure()`` is called after an sbuild failure:

1. **Extract** the failure section from the log (tail or error window, bounded by ``ai.max_log_lines``).
2. **Trigger pass** — regex-match every specialist's ``triggers.log_patterns`` against the excerpt.  On a hit, route straight to that specialist (no router call).
3. **Router pass** — invoke ``build-doctor`` with the artifact context.  It returns a :class:`DispatchPayload` with ``skill``, ``reason``, ``confidence``, ``evidence``, ``fallback_skills`` and ``extra_files_needed``.
4. **Validate the pick** — reject it unless the skill exists in the installed registry and ``confidence`` meets ``ai.router_min_confidence`` (default ``0.5``).  On rejection, walk ``fallback_skills`` in order, each gated the same way.
5. **Default fallback** — if nothing passes, run the generic ``build-patch`` skill.
6. **Run the specialist** — assemble context from its collectors, call the model, parse against its contract.
7. **Validate the output** — for ``patch`` contracts, ``git apply --check`` is mandatory before commit.  A single correction retry via the ``patch-correction`` skill handles fuzzing failures.

Three safety layers ensure a malformed or over-confident router response never derails triage: trigger pre-routing is LLM-free, the router's pick is validated against the installed registry, and the default fallback always wins if everything else fails.

Output contracts
----------------

A contract is a promise about the shape of the model's reply.  The runner parses against it and hands back a typed dataclass; callers never touch raw text.

.. list-table::
   :header-rows: 1

   * - Contract
     - Payload
     - Purpose
   * - ``patch``
     - :class:`PatchPayload` (``action``, ``patch_filename``, ``patch_content``, ``diagnosis``, ``explanation``)
     - A quilt patch (or ``NO_PATCH`` / ``REFRESH``).  Validated with ``git apply --check`` before commit.
   * - ``diagnosis``
     - :class:`DiagnosisPayload` (``can_drop``, ``diagnosis``, ``explanation``)
     - Yes/no judgement (used for "is this patch already upstream?").
   * - ``dispatch``
     - :class:`DispatchPayload` (``skill``, ``reason``, ``confidence``, ``evidence``, ``fallback_skills``, ``extra_files_needed``)
     - Router output.  Skills with this contract are excluded from the router's own menu.
   * - ``guidance``
     - :class:`GuidancePayload` (``diagnosis``, ``explanation``)
     - Text-only advice.  No automated action — used when the right answer is "don't patch, do X."

Router dispatch output
----------------------

``build-doctor`` doesn't just name a skill — it declares how sure it is and why.  Raw response::

    SKILL: python-compat
    REASON: distutils was removed in Python 3.12 and the log shows the exact ModuleNotFoundError
    CONFIDENCE: 0.95
    EVIDENCE: ModuleNotFoundError: No module named 'distutils'
    EVIDENCE: at setup.py line 3
    FALLBACK: build-patch

The runtime treats anything below ``ai.router_min_confidence`` as "don't commit yet, try fallbacks."  Fallback lookup rejects unknown skill names.  Evidence lines are captured for the audit trail.  ``EXTRA_FILES:`` is parsed (router can request more context) but not yet consumed — see the roadmap below.

How the skills menu is built
----------------------------

The ``build-doctor`` skill body contains the literal token ``{{skills_menu}}``.  When the runner invokes the router, :func:`_expand_templates` replaces that token with a rendered list:

1. :func:`list_skills` walks the skills directory and returns every subfolder containing a ``SKILL.md``.
2. :func:`_specialist_skills` loads each one and filters out anything whose ``output_contract`` is ``dispatch`` — so the router cannot see itself or any peer router.
3. :func:`render_skills_menu` formats them as ``- <name>: <description> — <when_to_use>``, sorted by name for deterministic output.

No caching.  The menu is rebuilt on every router call, which means dev iteration (edit a skill, rerun) works without restart.

Extensibility
-------------

**Adding a specialist:** drop a folder under ``src/packastack/skills/<name>/`` with a ``SKILL.md``.  The loader finds it, the router's ``{{skills_menu}}`` picks it up automatically, triggers compile at load time.  No Python edits.

**Overriding for development:** ``PACKASTACK_SKILLS_DIR=/some/path`` points the loader at an alternate directory.

**When you do need Python changes:**

- A genuinely new *shape* of output (not ``patch`` / ``diagnosis`` / ``dispatch`` / ``guidance``) means adding a parser in ``contracts.py``.
- A genuinely new *kind* of context (e.g. "read the last autopkgtest log") means adding a collector in ``collectors.py`` and referencing it in ``requires_context``.
- Routing policy changes (making a skill on-demand only, say) touch ``_select_specialist``.

In practice, the vast majority of new failure classes are "new pattern, emit a patch or guidance" — pure folder drops.

Current skill catalog
---------------------

.. list-table::
   :header-rows: 1

   * - Skill
     - Contract
     - Role
   * - ``build-doctor``
     - dispatch
     - Router
   * - ``build-patch``
     - patch
     - Generic patch author; safe fallback
   * - ``patch-diagnosis``
     - diagnosis
     - Decides whether an existing quilt patch is already upstream and can be dropped
   * - ``patch-refresh``
     - patch
     - Refreshes fuzzy patches after upstream drift, including ``setup.cfg`` → ``pyproject.toml`` migrations
   * - ``patch-correction``
     - patch
     - Fixes a patch that failed ``git apply --check``
   * - ``python-compat``
     - patch
     - Interpreter bumps (removed/moved stdlib APIs)
   * - ``library-sync-advisor``
     - guidance
     - Stale oslo / python-\*client / stevedore / keystoneauth1 — advises library sync instead of patching

Roadmap
-------

1. **Eval fixtures** — a corpus of real failed sbuild logs with expected router picks to guard routing quality as skills multiply.
2. **Consume** ``extra_files_needed`` — let the router request additional context and re-run instead of guessing.
3. ``ai capture`` **CLI** — snapshot a triage run into a fixture directory (feeds the eval corpus for free).
4. **More specialists** — ``dep-gate`` for dependencies not yet landed, ``packaging-advisor`` for structural shifts (e.g. eventlet removal).
5. **Approval mode** — pause before applying AI changes.
6. ``debian/`` **file skills** — opt-in specialists that can edit ``debian/control`` / ``debian/rules`` (currently prohibited).
