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

## AI-Powered Build Diagnosis

Packastack can use any OpenAI-compatible AI model to diagnose build failures and propose fixes. AI features are opt-in and activate when an API key is set. See [docs/reference/config.rst](docs/reference/config.rst) for full configuration details.

### What it can do today

- **Diagnose sbuild failures** — sends the build log, `debian/control`, and `debian/rules` to the AI, which returns a structured diagnosis.
- **Propose source-code patches** — the AI can produce unified diffs with DEP3 headers that fix build errors (e.g., missing imports, Python compatibility issues).
- **Validate patches before committing** — runs `git apply --check` and asks the AI for a corrected version if the patch doesn't apply cleanly.
- **Auto-drop upstreamed patches** — detects patches already in upstream via `git apply --check --reverse`, confirms with the AI, then removes the file, updates `debian/patches/series`, and commits.
- **Retry with memory** — after a failed AI-patched rebuild, saves the attempt history to `ai-memory.json` in the run directory so subsequent builds can tell the AI what was already tried.

### Current limitations

The AI system is focused on source-code fixes. Several classes of build problem require manual intervention:

| Gap | Detail |
|-----|--------|
| **No `debian/control` modification** | Cannot add or change build-dependencies, even when the diagnosis identifies a missing package. |
| **No `debian/rules` modification** | Cannot adjust build flags, override targets, or change the build system. |
| **No `debian/changelog` entry for AI patches** | Patches are committed but no changelog entry is generated. |
| **Single retry per build** | Only one patch-and-rebuild cycle runs per invocation; a second failure saves memory and exits. |
| **No build-dep resolution** | Can diagnose "missing libfoo-dev" but cannot install or add it to `Build-Depends`. |
| **Patch conflicts not auto-fixed** | When `gbp pq import` fails on a non-upstreamed patch, the AI provides a diagnosis but does not produce a corrected patch. |
| **No multi-arch awareness** | The same patch is proposed regardless of architecture; arch-specific fixes need human review. |
| **No interactive approval** | Valid patches are applied automatically; there is no preview or confirmation step. |
| **No API retry or cost tracking** | A single HTTP call is made per diagnosis; transient failures are not retried and token usage is not tracked. |

### Toward touchless packaging

Closing these gaps would allow Packastack to handle end-to-end packaging without human intervention:

1. **Packaging-file patches** — teach the AI to emit changes to `debian/control`, `debian/rules`, and `debian/changelog` in addition to source patches, then apply them the same way (validate, commit).
2. **Build-dependency resolution** — when the AI identifies a missing build-dep, automatically add it to `debian/control` and retry.
3. **Multi-cycle retry loop** — allow more than one patch-and-rebuild cycle per invocation with a configurable cap (e.g., `--ai-max-retries 3`).
4. **Patch conflict repair** — when `gbp pq import` fails, send the conflicting patch and the upstream diff to the AI to produce an updated patch.
5. **Changelog generation** — auto-generate a `d/changelog` entry for every AI-driven change (patch additions and drops).
6. **Approval mode** — add `--ai-approve` to pause before applying AI changes so operators can review patches interactively.
7. **Multi-arch support** — detect arch-specific failures and produce arch-gated patches when needed.
8. **API resilience** — add retry-with-backoff for transient API errors and optional token-budget limits.
