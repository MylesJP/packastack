# Packastack

Packastack is a small CLI tool to assist with building OpenStack packages for Ubuntu.

Commands implemented in this phase:

- `packastack init` — initialize configuration and cache directories, clone OpenStack releases, and optionally prime Ubuntu archive metadata.
- `packastack refresh ubuntu-archive` — fetch and cache Packages.gz indexes from an Ubuntu archive mirror, respecting TTL and offline mode.
- `packastack build <package>` — build a package and its dependencies.

Resume an interrupted build by reusing a previous workspace:

```bash
uv run packastack build cinder --resume                          # latest build
uv run packastack build cinder --resume-build 20260210-143022    # specific build
```

See `pyproject.toml` for development dependencies and test configuration.

## AI-Powered Build Diagnosis (build-doctor)

Packastack ships an AI-assisted triage system called **build-doctor**. When an sbuild fails and an API key is configured, build-doctor inspects the log, picks the best specialist skill for the failure shape, and either produces a validated patch or explains what the maintainer needs to do. AI features are opt-in and non-blocking: if the API call fails, you get the normal failure output. See [docs/reference/config.rst](docs/reference/config.rst) for configuration and [docs/explanation/ai-skills.rst](docs/explanation/ai-skills.rst) for the architecture.

### How it works

build-doctor is a deterministic Python dispatcher plus pluggable **skills** on disk. Skills are folders containing a `SKILL.md` file with YAML frontmatter (name, description, output contract, trigger regexes) and a prompt body. The dispatcher:

1. Extracts the failure section from the sbuild log.
2. Tries a cheap regex pass over every skill's `triggers.log_patterns`. A match routes directly — no router call.
3. Otherwise asks the `build-doctor` router skill to pick a specialist. The router returns a structured dispatch payload with a confidence score, evidence lines, and optional fallbacks.
4. Validates the pick against a confidence threshold (default 0.5, configurable) and the installed skill registry; walks the fallback chain if needed; drops to the generic `build-patch` skill as a last resort.
5. Runs the chosen specialist, parses its response against its declared output contract, validates any patch with `git apply --check` (with one correction retry via the `patch-correction` skill), and commits the result.

**Adding a new failure-class handler is a folder drop** — no Python changes. Drop `src/packastack/skills/<name>/SKILL.md` in place (or point `PACKASTACK_SKILLS_DIR` at a dev copy), and the router picks it up at next run.

### Skills shipped today

| Skill | Output | Role |
|---|---|---|
| `build-doctor` | dispatch | Router — picks the specialist |
| `build-patch` | patch | Generic patch author; safe fallback |
| `patch-diagnosis` | diagnosis | Decides whether an existing quilt patch can be dropped (already upstream?) |
| `patch-refresh` | patch | Refreshes fuzzy patches after upstream drift, including `setup.cfg` → `pyproject.toml` migrations |
| `patch-correction` | patch | Fixes a patch that failed `git apply --check` |
| `python-compat` | patch | Handles interpreter bumps (distutils/imp/cgi removed, `ast.Str` → `Constant`, `asyncio.coroutine` removed, etc.) |
| `library-sync-advisor` | guidance | Detects stale oslo / python-\*client / stevedore / keystoneauth1 dependencies and advises a library sync instead of a patch |

### What it handles well today

- **Python interpreter bumps** — routed to `python-compat`, which knows the common removed-stdlib replacements.
- **Fuzzy quilt patches** — routed to `patch-refresh`; produces an updated patch against current upstream.
- **Upstreamed patches** — `patch-diagnosis` + auto-drop: removes the file, updates `debian/patches/series`, and commits.
- **Stale dependencies** — `library-sync-advisor` explicitly refuses to patch and tells you which src:package to sync.
- **Retry with memory** — failed attempts are saved to `ai-memory.json` in the run directory so subsequent builds can tell skills what was already tried.

### Current limitations

| Gap | Detail |
|-----|--------|
| **No `debian/` file edits** | Skills are restricted to upstream-source patches; changing `debian/control`, `debian/rules`, or `debian/changelog` requires a human. |
| **No build-dep auto-resolution** | A skill can diagnose "missing libfoo-dev" via guidance, but cannot install or add it to `Build-Depends`. |
| **Single patch-and-rebuild cycle** | One retry per build; a second failure saves memory and exits. |
| **No multi-arch awareness** | Arch-specific fixes need human review. |
| **No interactive approval** | Valid patches are applied automatically; no preview step. |
| **No API retry or cost tracking** | Single HTTP call per skill invocation; transient failures are not retried. |
| **`extra_files_needed` parsed but unused** | The router can ask for additional context, but the runner doesn't yet re-run with it. |

### Roadmap

1. **Eval fixtures** — a corpus of real failed sbuild logs with expected router picks to guard routing quality as skills multiply.
2. **Consume `extra_files_needed`** — when the router asks for `debian/patches/series` etc., re-run with them instead of guessing.
3. **`ai capture` CLI** — snapshot a triage run into a fixture directory.
4. **More specialists** — `dep-gate` (guidance for dependencies not landed yet), `packaging-advisor` (structural shifts like eventlet removal).
5. **Approval mode** — pause before applying AI changes so operators can review.
6. **`debian/` file skills** — let specialists emit `debian/control` / `debian/rules` edits behind an explicit opt-in.
