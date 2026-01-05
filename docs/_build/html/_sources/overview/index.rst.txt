Overview
========

PackaStack is Canonical’s tool for building, testing, and validating OpenStack packages for Ubuntu. It’s for engineers who want deterministic builds without becoming part-time yak herders. PackaStack orchestrates schroots, curates a local APT repo, and draws hard lines between online and offline so your Tuesday builds behave like your release builds (and nobody has to whisper to the CI server).

This section is the high-level map: what PackaStack is, what it refuses to be, and how it fits into your development or CI pipeline. If you’re new, start here to learn the promises we keep and the shortcuts we avoid. Think of it as the “terms and conditions” you actually want to read.

.. toctree::
   :maxdepth: 1
   :hidden:

   invariants
