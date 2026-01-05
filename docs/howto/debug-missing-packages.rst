How to Debug Missing Packages
=============================

Missing packages are a classic way to ruin an otherwise good day. PackaStack gives you a few flashlights so you can find the gap quickly and get back to shipping.

Symptoms
--------
- Build fails with “package not found” or similar errors
- Tests fail due to missing dependencies
- APT reports unsatisfiable dependencies inside the schroot

Step-by-Step Debugging
----------------------
1. **Check the local repo**: Ensure the package was built and published to the local repo (trust, but verify).

   .. code-block:: bash

      tree <workspace>/localrepo

2. **Check pinning**: Ensure the local repo is pinned with highest priority. If not, APT will happily wander off to the public archive.

   .. code-block:: bash

      grep -r Pin-Priority <workspace>/localrepo/apt.conf.d/

3. **Inspect schroot APT sources**: Enter the schroot and check sources and pins. This is the “what are you really installing?” step.

   .. code-block:: bash

      sudo schroot -c packastack-noble-amd64 -u root -- bash
      cat /etc/apt/sources.list
      cat /etc/apt/preferences.d/*

4. **Rebuild or refresh**: If the package is missing, rebuild it or refresh the schroot and local repo. If you’re still stuck after that, blame DNS for a minute—it feels good.

If you’re still stuck, file a bug with logs and details. Sometimes, the package you’re looking for was inside you all along (but usually it’s just a typo).

See also: :doc:`../reference/cli` for flags, especially offline/pinning options, and :doc:`../reference/cli-contract` for exit codes you can trust.
