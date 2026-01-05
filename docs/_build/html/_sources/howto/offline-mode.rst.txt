How to Use Offline Mode
=======================

PackaStack’s offline mode is for the days when the network is a luxury or a liability. Or perhaps you want to melt your laptop on a long-haul flight—offline mode will save your tail. This guide shows how to prep the workspace, pre-seed everything you’ll need, and prove you can pull the plug without breaking a sweat.

Why Offline Mode?
-----------------

Offline mode keeps builds reproducible and calm in air-gapped or high-security environments. It also forces you to discover missing pieces while you still have a cable plugged in, not during the go/no-go meeting.

Preparing for Offline
---------------------

Do the homework while you still have network:

1. Initialize once to create config, caches, and clone ``openstack/releases``:

   .. code-block:: bash

      packastack init --prime

2. Refresh Ubuntu archive indexes for your target series (defaults hit release/updates/security and main/universe):

   .. code-block:: bash

      packastack refresh --ubuntu-series noble

3. Run at least one online build for your package to populate tarballs and the local repo:

   .. code-block:: bash

      packastack build nova --series noble --arch amd64

Running an Offline Build
------------------------

Once pre-seeded, you can run:

.. code-block:: bash

   packastack build nova --offline --series noble --arch amd64

PackaStack will block all network access inside the schroot. If anything is missing, you’ll get a clear error message. If the build completes, you know your environment is fully self-contained.

Validating Offline Correctness
------------------------------

Physically disconnect (or disable Wi-Fi) and run the offline build. If it passes, you’re ready for air-gapped deployment. If it fails, reconnect, rerun online to fill the gaps, then try again offline.

Troubleshooting
---------------
- If you see errors about missing tarballs or packages, re-run the build once online to let PackaStack fetch what it needs, or re-run ``packastack refresh`` to update indexes.
- If you discover a way to download something in offline mode, please file a bug (and consider a career in magic).

See also: offline flags in :doc:`../reference/cli` and the contract matrix in :doc:`../reference/cli-contract`.
