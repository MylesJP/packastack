# Packastack

Packastack is a small CLI tool to assist with building OpenStack packages for Ubuntu.

Commands implemented in this phase:

- `packastack init` — initialize configuration and cache directories, clone OpenStack releases, and optionally prime Ubuntu archive metadata.
- `packastack refresh ubuntu-archive` — fetch and cache Packages.gz indexes from an Ubuntu archive mirror, respecting TTL and offline mode.
- `packastack build <package>` — build a package and its dependencies.

Resume an interrupted build by reusing a previous workspace:

```bash
uv run packastack build cinder --resume-run-id 20260120T215453Z-build-59cd38a6
```

See `pyproject.toml` for development dependencies and test configuration.
