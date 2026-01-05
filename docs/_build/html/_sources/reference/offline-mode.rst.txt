Offline Mode Contract
=====================

Offline mode in PackaStack means exactly what it says: no network access is permitted during build or test phases. This ensures reproducibility, security, and the ability to build in air-gapped environments. If you try to sneak a download, PackaStack will catch you (and politely refuse).

All network interfaces are disabled or firewalled inside the schroot during build and test. Any attempt to access the network will fail immediately. Only pre-seeded resources (e.g., cached tarballs, local repo packages) are available.

All required source tarballs, build dependencies, and test data must be present in the workspace or local repo before starting an offline build. Prepare while online by running ``packastack init`` (optionally ``--prime``), refreshing indexes with ``packastack refresh``, and performing at least one online ``packastack build`` for your target package to populate caches and the local repo.

Building, testing, and installing packages using only local resources works. Any operation that requires downloading from the internet, including missing dependencies, unseeded tarballs, or external test data, will fail. If a required resource is missing, PackaStack will emit a clear error and halt.

All outbound and inbound network traffic is blocked in the schroot. DNS resolution is disabled. Attempts to use curl, wget, apt, or git to access remote resources will fail.

After pre-seeding, run:

.. code-block:: bash

   packastack build nova --offline --series noble --arch amd64

If the build completes without network access, your environment is correctly pre-seeded. For extra paranoia, unplug your network cable or disable Wi-Fi and try again.

Contractual Guarantees
----------------------
- "Offline" means no network access is permitted during build or test phases.
- All required resources must be pre-seeded; missing resources cause explicit failure.
- Network access is blocked at the schroot and host level.
- Validation steps are provided to confirm offline correctness.

See also: :doc:`schroot`, :doc:`local-repo`, :doc:`bind-mounts`, :doc:`../overview/invariants`
