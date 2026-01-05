.. PackaStack documentation master file, created from Canonical Sphinx Docs Starter Pack

Welcome to PackaStack!
======================

PackaStack is Canonical’s tool for hands-free, repeatable building, testing, and validating of OpenStack packages for Ubuntu. Think of it as an orchestra conductor that also makes coffee: it wrangles schroots, curates a local APT repo, enforces strict offline/online boundaries, and plugs in testing so you get consistent results without the late-night soldering iron. Use this site to set up, automate, and trust your packaging workflow—and maybe laugh once or twice along the way.

Jump to: :doc:`tutorial/quickstart` | :doc:`howto/index` | :doc:`reference/index` | :doc:`explanation/index`

Start with the :doc:`overview/index` to see what PackaStack guarantees and how it fits into your process. When you’re ready to build, follow the :doc:`tutorial/index` for the guided path from first-time setup to a smugly tested build. For targeted tasks—offline mode, schroot refresh, debugging, inspection, or cleanup—jump to the :doc:`howto/index`. When you need exact behavior and contracts, see the :doc:`reference/index`. For design rationale and mental models, visit :doc:`explanation/index`. Finally, keep the :doc:`appendices/index` handy for checklists and failure signatures (because even heroes need a field guide).

Implemented vs planned: everything documented under :doc:`reference/index` exists today. Ideas like dedicated schroot/admin commands or auto-init wizards will join the contract only when they ship. If you don’t see it here, it’s still a twinkle, not a promise.

.. toctree::
   :maxdepth: 2
   :hidden:

   overview/index
   tutorial/index
   howto/index
   reference/index
   explanation/index
   appendices/index
