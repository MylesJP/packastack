Explanation
===========

This section explains PackaStack’s architecture, design trade-offs, reproducibility model, testing philosophy, and contract guarantees. Use it to understand why the system behaves the way it does, and how to reason about changes or extensions. For the hands-on path, see :doc:`../tutorial/quickstart`; for exact behavior and contracts, visit :doc:`../reference/index`; for task-first recipes, head to :doc:`../howto/index`.

Implemented vs planned: everything here reflects current behavior unless explicitly marked “planned.” Missing CLI verbs (like a schroot subcommand) are intentionally deferred until we can guarantee good UX and safety.

.. toctree::
	:maxdepth: 1
	:hidden:

	walkthrough
	building-packages
	dependency-resolution
	architecture
	design-tradeoffs
	reproducibility
	testing-philosophy
