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
     - Build workspaces and exported sources
     - ``~/.cache/packastack/build``
   * - ``runs_root``
     - Run logs and summaries
     - ``~/.cache/packastack/runs``
   * - ``upload_ppa``
     - PPA to automatically upload to when ``--ppa-upload`` is used.
     - ``None``

Notes
-----
- These paths are expanded and resolved when PackaStack starts.
- Changing paths does not migrate existing data; move or clean caches manually if needed.
