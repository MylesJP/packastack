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

Notes
-----
- These paths are expanded and resolved when PackaStack starts.
- Changing paths does not migrate existing data; move or clean caches manually if needed.
