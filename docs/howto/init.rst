Initialize PackaStack
======================

``packastack init`` is the handshake that turns an empty directory into a polite, well-prepared build lab. Run it once per workspace to lay down config, caches, and the local ``openstack/releases`` checkout. It is cheerfully idempotent; re-running just dusts the shelves without overturning your custom settings.

Basic usage
-----------
Say hello and let PackaStack set the table:

.. code-block:: bash

   packastack init

What it does
------------
Behind the curtain, init quietly writes a default ``~/.config/packastack/config.yaml`` (only if you don’t already have one), creates cache directories under ``~/.cache/packastack`` (including ``runs`` and ``ubuntu-archive``), clones or updates ``https://opendev.org/openstack/releases``, drops a couple of metadata breadcrumbs (``README.txt`` and ``config.json``) into ``ubuntu-archive``, and notes the current Ubuntu development series so later commands know which way is north.

Optional priming
----------------

Feeling eager? Add ``--prime`` to vacuum up Ubuntu archive metadata right away (release, updates, security; main and universe; host and all arches):

.. code-block:: bash

   packastack init --prime

This is the same machinery as ``packastack refresh`` with the TTL set to zero; it insists on the newest indexes.

When to re-run
--------------
Hit init again after deleting caches, hopping to a new machine, or before going offline so the metadata cupboard is stocked. It’s also a safe way to refresh ``openstack/releases`` without kicking off a full build.

Troubleshooting
---------------
If the ``openstack/releases`` clone hiccups, re-run while online—PackaStack will grumble in the logs but carry on. To pair a fresh init with a clean schroot, delete the schroot using system tools and start a build; PackaStack will conjure a new one on demand.

See also: :doc:`../reference/cli` for full command flags and :doc:`../reference/cli-contract` for the stability matrix.
