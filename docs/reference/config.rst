Configuration Reference
=======================

PackaStack reads configuration from ``~/.config/packastack/config.yaml``. Values in that file override defaults and control where caches, workspaces, and repositories live.

Paths
-----

The ``paths`` section defines all on-disk locations used by PackaStack:

.. list-table::
   :header-rows: 1

   * - Key
     - Purpose
     - Default
   * - ``cache_root``
     - Base directory for PackaStack caches
     - ``~/.cache/packastack``
   * - ``openstack_releases_repo``
     - Local clone of ``openstack/releases``
     - ``~/.cache/packastack/openstack-releases``
   * - ``ubuntu_archive_cache``
     - Cached Ubuntu Packages indexes and metadata
     - ``~/.cache/packastack/ubuntu-archive``
   * - ``local_apt_repo``
     - Local APT repository published by builds
     - ``~/.cache/packastack/apt-repo``
   * - ``upstream_tarballs``
     - Cached upstream tarballs and extractions
     - ``~/.cache/packastack/upstream-tarballs``
   * - ``build_root``
     - Build workspaces, logs, and reports (per-package layout)
     - ``~/.cache/packastack/build``
   * - ``upload_ppa``
     - PPA to automatically upload to when ``--ppa-upload`` is used.
     - ``None``

Managed Packages
----------------

PackaStack automatically fetches the list of managed packages from the Ubuntu Cloud Archive team's authoritative source:

  https://git.launchpad.net/~ubuntu-cloud-archive/+git/pkg-scripts

This repository contains two files that define which packages the team manages:

- ``current-projects``: Core OpenStack services (nova, neutron, keystone, etc.)
- ``dependencies``: Python libraries and clients (oslo.*, python-*client, etc.)

These lists are fetched during ``packastack init`` and ``packastack refresh``, then cached locally at ``~/.cache/packastack/managed-packages.txt``. When building with ``build --all``, ``build libraries``, or ``build clients``, only packages in this list are built—everything else discovered from Launchpad or openstack/releases is skipped.

To update the managed packages list manually, run:

.. code-block:: bash

   packastack refresh

Git Configuration
-----------------

The ``git`` section controls how PackaStack interacts with Launchpad git repositories:

.. list-table::
   :header-rows: 1

   * - Key
     - Purpose
     - Default
   * - ``launchpad_username``
     - Your Launchpad username for SSH access
     - ``None``

When ``launchpad_username`` is set, PackaStack clones packaging repositories via SSH instead of HTTPS:

.. code-block:: yaml

   git:
     launchpad_username: your-launchpad-id

This requires SSH keys to be configured for Launchpad. To set up SSH access:

1. Generate an SSH key if you don't have one: ``ssh-keygen -t ed25519``
2. Add your public key to Launchpad: https://launchpad.net/~/+editsshkeys
3. Test access: ``ssh -T your-launchpad-id@git.launchpad.net``

With SSH configured, PackaStack uses URLs like:
``git+ssh://your-launchpad-id@git.launchpad.net/~ubuntu-openstack-dev/ubuntu/+source/nova``

Existing repositories cloned via HTTPS are automatically upgraded to SSH on their next fetch when you add a ``launchpad_username``.

AI-Powered Build Diagnosis (build-doctor)
-----------------------------------------

PackaStack ships an AI-assisted triage system called **build-doctor**.  When a build fails and an API key is configured, build-doctor routes the failure to a specialist skill that either produces a validated patch (with DEP3 headers) or provides a text explanation.  The dispatcher uses any AI model exposed via an OpenAI-compatible ``/v1/chat/completions`` endpoint.

See :doc:`../explanation/ai-skills` for the architecture and :doc:`../howto/add-an-ai-skill` for how to add your own specialist.

AI features are **opt-in** — they activate automatically when an API key is set and are completely non-blocking: if the API call fails, the normal failure output is shown.

Setting an API key
^^^^^^^^^^^^^^^^^^

The recommended way is via an environment variable.  PackaStack checks these in order:

1. ``PACKASTACK_AI_API_KEY`` — project-specific (highest priority)
2. ``OPENAI_API_KEY`` — standard OpenAI convention
3. ``ANTHROPIC_API_KEY`` — backward compatibility

.. code-block:: bash

   # Add to your shell profile (~/.bashrc, ~/.zshrc, etc.)
   export OPENAI_API_KEY="sk-..."

Alternatively, set the key in ``config.yaml`` (less recommended — keeps credentials on disk):

.. code-block:: yaml

   ai:
     api_key: "sk-..."

Choosing a provider and model
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The ``ai`` section controls which model endpoint PackaStack uses:

.. list-table::
   :header-rows: 1

   * - Key
     - Purpose
     - Default
   * - ``api_key``
     - API key (env vars take precedence)
     - ``None``
   * - ``base_url``
     - OpenAI-compatible API base URL
     - ``https://openrouter.ai/api/v1``
   * - ``model``
     - Model identifier (browse at https://openrouter.ai/models)
     - ``anthropic/claude-sonnet-4-5-20250929``
   * - ``max_tokens``
     - Maximum response tokens
     - ``8192``
   * - ``timeout``
     - HTTP request timeout in seconds
     - ``120``
   * - ``router_min_confidence``
     - Minimum router confidence (``0.0``–``1.0``) to accept the primary specialist pick.  Below this, build-doctor walks the router's fallback list and finally drops to ``build-patch``.
     - ``0.5``
   * - ``max_log_lines``
     - Maximum lines of sbuild log to include in context.  ``0`` means no limit.
     - ``0``

The ``base_url`` can also be set via the ``PACKASTACK_AI_BASE_URL`` environment variable.

Loading skills from a custom directory
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Skills live under ``src/packastack/skills/<name>/SKILL.md`` inside the installed package.  To iterate on a skill without reinstalling, point ``PACKASTACK_SKILLS_DIR`` at an alternate directory:

.. code-block:: bash

   export PACKASTACK_SKILLS_DIR=/path/to/my-skills

The override directory must contain one subfolder per skill, each with its own ``SKILL.md``.  When unset, PackaStack uses the skills shipped in the wheel.

Provider examples
^^^^^^^^^^^^^^^^^

**Anthropic Claude via OpenRouter** (default — no config changes needed):

.. code-block:: bash

   export PACKASTACK_AI_API_KEY="sk-or-..."

**OpenAI (direct):**

.. code-block:: bash

   export OPENAI_API_KEY="sk-..."

.. code-block:: yaml

   ai:
     base_url: "https://api.openai.com/v1"
     model: "gpt-4o"

**Local model via Ollama:**

.. code-block:: bash

   export PACKASTACK_AI_API_KEY="local"      # any non-empty value
   export PACKASTACK_AI_BASE_URL="http://localhost:11434/v1"

.. code-block:: yaml

   ai:
     base_url: "http://localhost:11434/v1"
     model: "llama3"

**Other OpenAI-compatible providers** (Together, Groq, vLLM, etc.) work the same way — set ``base_url`` to the provider's endpoint and ``model`` to the model identifier.

Disabling AI
^^^^^^^^^^^^

To disable AI for a single build without removing your key:

.. code-block:: bash

   packastack build <package> --no-ai

To disable it permanently, simply do not set an API key.

Notes
-----
- These paths are expanded and resolved when PackaStack starts.
- Changing paths does not migrate existing data; move or clean caches manually if needed.
