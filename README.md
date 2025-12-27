# Packastack

Packastack is a small CLI tool to assist with building OpenStack packages for Ubuntu.

Commands implemented in this phase:

- `packastack init` — initialize configuration and cache directories, clone OpenStack releases, and optionally prime Ubuntu archive metadata.
- `packastack refresh ubuntu-archive` — fetch and cache Packages.gz indexes from an Ubuntu archive mirror, respecting TTL and offline mode.

See `pyproject.toml` for development dependencies and test configuration.
