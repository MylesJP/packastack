Add an AI Skill
===============

When you encounter a new class of build failure that the existing skill catalog doesn't handle well, you can add a new specialist skill without touching Python.  This guide walks through the common case: a new failure signature that should produce either a quilt patch or plain-text guidance.

For the architecture behind all this, see :doc:`../explanation/ai-skills`.

When adding a skill is enough
-----------------------------

A folder drop is sufficient when the new skill:

- Emits a known output shape — ``patch``, ``diagnosis``, ``dispatch``, or ``guidance``.
- Uses context the existing collectors already provide (``failure_header``, ``sbuild_log_tail``, ``working_tree``, ``ai_memory``).
- Is triggered by router dispatch or by log-line regex triggers — not by some other event type.

If you need a new output shape, a new source of context, or a non-routing invocation path, see :ref:`python-changes` below.

Walkthrough
-----------

Say we want a specialist that handles Sphinx documentation build failures.

Step 1 — create the folder
^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: bash

   mkdir -p src/packastack/skills/sphinx-doc-failure

Step 2 — write ``SKILL.md``
^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: yaml

   ---
   name: sphinx-doc-failure
   description: Patch upstream source or disable a doc build step when Sphinx fails during package build
   version: 1
   output_contract: patch
   when_to_use: Use when a build failure comes from Sphinx — missing extensions, deprecated directives, or a docs target returning non-zero.
   requires_context:
     - failure_header
     - sbuild_log_tail
     - working_tree
     - ai_memory
   triggers:
     log_patterns:
       - "sphinx-build.*ERROR"
       - "Extension error \\(sphinx"
       - "unknown directive type"
       - "Could not import extension"
   ---
   You are a Debian packaging expert specialising in Ubuntu OpenStack packages.

   The build failed because Sphinx could not render the package's documentation.  Produce a single quilt patch that either fixes the upstream usage or disables the offending doc target until upstream catches up.

   CONSTRAINTS:
   - Produce ONE quilt patch that fixes the immediate failure.
   - Patch upstream source only — never edit files under ``debian/``.
   - The patch MUST apply cleanly with ``git apply --check`` against the source tree you were shown.
   - DEP3 headers required: Description, Author, Forwarded, Last-Update.

   Respond in this exact format:

   DIAGNOSIS: <one-line summary>
   ACTION: QUILT_PATCH | NO_PATCH
   EXPLANATION: <2-5 sentences>
   PATCH_FILENAME: <name>.patch
   --- BEGIN PATCH ---
   <DEP3 headers followed by unified diff>
   --- END PATCH ---

Three things to get right:

1. **``name`` matches the folder name** (case-sensitive).  The router uses this name verbatim.
2. **``output_contract: patch``** — this is what tells the runner to parse the reply as a :class:`PatchPayload` and run ``git apply --check`` before committing.  Use ``guidance`` instead if the right answer is "don't patch — do X."
3. **Trigger regexes** must be valid Python regex and escape YAML-sensitive characters correctly (note the doubled backslashes above).

Step 3 — smoke-test the skill loads
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: bash

   uv run python -c "
   from packastack.ai.skills import load_skill
   from packastack.ai.triggers import match_triggers
   s = load_skill('sphinx-doc-failure')
   print(s.name, s.output_contract)
   print(match_triggers([s], 'sphinx-build ERROR: extension X failed to import'))
   "

Expected output::

    sphinx-doc-failure patch
    sphinx-doc-failure

If the loader raises ``SkillFormatError``, your frontmatter is malformed.  If ``match_triggers`` returns ``None`` on a sample line, your regex is wrong.

Step 4 — add a test
^^^^^^^^^^^^^^^^^^^

Add a smoke test to ``tests/ai/test_skills.py`` mirroring the pattern used by the other skills, and extend ``test_list_skills_includes_all_installed`` with the new name.

Step 5 — install and run
^^^^^^^^^^^^^^^^^^^^^^^^

For dev iteration without reinstalling, point PackaStack at your working tree:

.. code-block:: bash

   export PACKASTACK_SKILLS_DIR=$PWD/src/packastack/skills

For a real deploy, rebuild the wheel — ``hatchling`` bundles ``*.md`` under ``src/packastack/skills/`` automatically via ``artifacts`` in ``pyproject.toml``.

On the next ``packastack build`` with a failing Sphinx target, build-doctor sees ``sphinx-doc-failure`` in its ``{{skills_menu}}`` and routes to it — or trigger pre-routing matches first and skips the router entirely.

Tips
----

- **Be specific in ``when_to_use``.**  The router reads this verbatim to decide when your skill fits.  "Use when …" clauses that enumerate concrete error strings route better than vague descriptions.
- **Prefer trigger regexes for unambiguous signatures.**  They skip the router call, which is cheaper and more stable than LLM classification.  The router remains the safety net for ambiguous cases.
- **Tell the skill what NOT to do.**  Constraints like "never edit ``debian/``" or "respond NO_PATCH if the fix needs a new dependency" keep output usable.
- **Prescribe the output format exactly.**  The runner parses by looking for literal headers (``ACTION:``, ``PATCH_FILENAME:``, ``--- BEGIN PATCH ---``).  Any deviation is a parse failure.
- **Guidance over bad patches.**  When the correct fix is a library sync, a new build-dep, or a structural refactor, use ``output_contract: guidance`` and describe what the maintainer should do.  Don't let the model invent a patch it can't back up.

.. _python-changes:

When you need Python changes
----------------------------

Three cases require editing Python, not just adding a skill:

1. **New output shape** — if the skill needs to produce something other than a patch, yes/no judgement, router pick, or free-text guidance.  Add a parser in ``packastack/ai/contracts.py`` and a payload dataclass.
2. **New context source** — if your skill needs data none of the existing collectors provide (e.g. PyPI metadata, an autopkgtest log, an apt cache query).  Register a new collector in ``packastack/ai/collectors.py`` and reference it in ``requires_context``.
3. **Non-routing invocation** — if the skill should run outside the normal dispatcher flow (e.g. on demand, on a schedule).  Call :func:`run_skill` directly from wherever the new trigger lives.

Each of these is a small, isolated change.  The common case stays a pure folder drop.
