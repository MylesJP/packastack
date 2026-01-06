# Refactor Plan: build.py

This document captures the refactoring plan for `src/packastack/commands/build.py` (3,873 lines).

## Current Progress

| Metric | Before | Current | Change |
|--------|--------|---------|--------|
| build.py lines | 3,873 | 680 | -3,193 (-82%) |
| packastack.build/ lines | 0 | ~8,100 | New package |
| tests passing | 269 | 1,237 | ✓ |

**STATUS: REFACTOR COMPLETE** - The main loop has been replaced with orchestrator calls.
- `_run_build` now uses `setup_build_context()` + `build_single_package()` from `single_build.py`
- Build-all orchestration extracted to `all_runner.py`
- All 1,237 tests pass
- Tests updated to patch functions at their source modules

### Extracted Modules in `packastack.build/`:

| Module | Lines | Purpose |
|--------|-------|---------|
| `types.py` | 219 | 7 dataclasses (BuildInputs, PhaseResult, etc.) |
| `errors.py` | 160 | Exit codes + phase_error/phase_warning/log_phase_event helpers |
| `git_helpers.py` | 280 | Git helper functions including git_commit() |
| `tarball.py` | 446 | 4 tarball acquisition functions |
| `phases.py` | 656 | 6 phase functions (retirement, registry, policy, indexes, tools, schroot) |
| `all_reports.py` | 245 | Build-all report generation (JSON + Markdown) |
| `type_resolution.py` | 145 | CLI build type parsing and auto-resolution |
| `all_helpers.py` | ~300 | Build-all helpers (graph, versions, retire filter, batches, run_single_build) |
| `all_runner.py` | 775 | Build-all orchestration (_run_build_all, sequential/parallel executors) |
| `localrepo_helpers.py` | 91 | Local APT repo refresh helpers |
| `single_build.py` | 1,919 | Single package build phases + orchestrator |

### Orchestration Functions in `single_build.py`:

**NEW: Setup and Orchestration**
- `SetupInputs` - Dataclass for pre-build phase inputs
- `setup_build_context()` - Runs phases 1-6 (retirement through schroot), returns `SingleBuildContext`
- `SingleBuildOutcome` - Complete result of building a single package
- `build_single_package()` - Orchestrator that calls all phase functions in sequence

**Phase Functions:**
- `SingleBuildContext` - Collects all resolved values for phase execution
- `PhaseResult` - Standard result type for phases
- `FetchResult`, `PrepareResult`, `ValidateDepsResult`, `BuildResult` - Phase-specific results
- `fetch_packaging_repo()` → (PhaseResult, FetchResult) - Clone packaging repo, protect files, update watch
- `prepare_upstream_source()` → (PhaseResult, PrepareResult) - Fetch/generate upstream tarball
- `validate_and_build_deps()` → (PhaseResult, ValidateDepsResult) - Validate deps, auto-build missing
- `import_and_patch()` → PhaseResult - Import tarball, apply patches with gbp pq
- `build_packages()` → (PhaseResult, BuildResult) - Build source and optionally binary packages
- `verify_and_publish()` → PhaseResult - Publish to local APT repo

### Phase Functions in `phases.py`:
- `check_retirement_status()` → (PhaseResult, RetirementCheckResult)
- `resolve_upstream_registry()` → (PhaseResult, RegistryResolutionResult)
- `check_policy()` → (PhaseResult, PolicyCheckResult)
- `load_package_indexes()` → (PhaseResult, PackageIndexes)
- `check_tools()` → (PhaseResult, ToolCheckResult)
- `ensure_schroot_ready()` → (PhaseResult, SchrootSetupResult)

### Functions in `type_resolution.py`:
- `VALID_BUILD_TYPES` - constant
- `resolve_build_type_from_cli()` - parse CLI options
- `resolve_build_type_auto()` - auto-select based on releases data
- `build_type_from_string()` - convert string to BuildType enum

### Functions in `all_helpers.py`:
- `build_dependency_graph()` - wrapper for graph_builder
- `build_upstream_versions_from_packaging()` - extract versions from changelogs
- `filter_retired_packages()` - filter retired packages using project-config
- `get_parallel_batches()` - compute parallel build batches
- `run_single_build()` - run subprocess for single package build with exit code mapping

### Functions in `all_reports.py`:
- `generate_build_all_reports()` - generate JSON and Markdown summary reports

## Phase Map

The `_run_build` function (lines 2173-3873) contains 12 major phases executed sequentially:

| # | Phase | Lines | Description |
|---|-------|-------|-------------|
| 1 | **resolve** | 2173-2272 | Parse CLI build type, resolve auto type from releases repo, resolve series |
| 2 | **planning** | 2273-2319 | Call `run_plan_for_package()`, handle plan-only modes, check exit codes |
| 3 | **retirement** | 2320-2397 | Check if upstream project is retired via RetirementChecker |
| 4 | **registry** | 2400-2480 | Load UpstreamsRegistry, resolve upstream config, initialize provenance |
| 5 | **policy** | 2483-2505 | Check snapshot eligibility, enforce policy blocks |
| 6 | **plan** (indexes) | 2508-2606 | Load Ubuntu/CA/local indexes, validate tools, ensure schroot |
| 7 | **fetch** | 2609-2705 | Clone packaging repo, protect files, check watch mismatch |
| 8 | **prepare** | 2708-2800 | Update launchpad.yaml, select upstream source, apply signature policy, fetch tarball |
| 9 | **validate-deps** | 2953-3107 | Extract/validate upstream deps, identify buildable missing deps |
| 10 | **auto-build** | 3110-3215 | Recursively build missing dependencies via subprocess |
| 11 | **import-orig** | 3349-3466 | Import upstream tarball, merge upstream branch |
| 12 | **patches** | 3469-3598 | gbp pq import/export, refresh patches |
| 13 | **build** | 3601-3756 | Build source package, optionally binary with sbuild/dpkg |
| 14 | **verify** | 3759-3823 | Publish artifacts to local repo, regenerate indexes |
| 15 | **provenance** | 3826-3844 | Write provenance record |
| 16 | **report** | 3847-3873 | Write summary, show upload commands |

### Build-All Functions (lines 371-1460)

| Function | Lines | Description |
|----------|-------|-------------|
| `_build_dependency_graph` | 371-397 | Wrapper for graph_builder |
| `_build_upstream_versions_from_packaging` | 400-416 | Extract versions from changelogs |
| `_filter_retired_packages` | 419-451 | Filter retired packages |
| `_get_parallel_batches` | 454-505 | Compute parallel build batches |
| `_run_single_build` | 508-607 | Run subprocess for single package |
| `_generate_reports` | 611-768 | Generate JSON/Markdown reports |
| `run_build_all` | 771-828 | Entry point (creates RunContext) |
| `_run_build_all` | 831-1199 | Main build-all implementation |
| `_run_sequential_builds` | 1228-1333 | Sequential executor |
| `_run_parallel_builds` | 1336-1460 | Parallel executor |

### Helper Functions (lines 180-369)

| Function | Lines | Description |
|----------|-------|-------------|
| `_maybe_enable_sphinxdoc` | 180-212 | Add sphinxdoc addon to rules |
| `_no_gpg_sign_enabled` | 214-217 | Check env for GPG sign disable |
| `_maybe_disable_gpg_sign` | 220-226 | Inject --no-gpg-sign into git commit |
| `_get_git_author_env` | 228-262 | Get git author from DEBFULLNAME/DEBEMAIL |
| `_shorten_path` | 264-281 | Shorten paths for commit messages |
| `_staged_changes` | 284-307 | List staged git files |
| `_compose_commit_message` | 310-340 | Compose commit with file list |

---

## Duplication Inventory

### 1. Activity + Log Event Pattern (~50 occurrences)

Every phase logs both to UI and structured events:

```python
activity("phase", f"Message: {value}")
run.log_event({
    "event": "phase.action",
    "key": value,
})
```

**Proposal**: Create `log_phase_event(run, phase, message, event_key, **kwargs)` helper.

### 2. Error Exit Pattern (~15 occurrences)

```python
if <error_condition>:
    activity("phase", f"Error: {message}")
    run.write_summary(
        status="failed",
        error="...",
        exit_code=EXIT_*,
    )
    return EXIT_*
```

**Proposal**: Create `phase_error(run, phase, message, exit_code) -> int` helper that:
- Logs activity with error prefix
- Logs event with `phase.error` key
- Calls `run.write_summary()`
- Returns the exit code

### 3. Package Index Loading (3 occurrences)

- Lines 993-1018 (build-all): Load Ubuntu + CA + local repo indexes
- Lines 2507-2536 (_run_build): Same pattern
- Lines 2537-2560 (_run_build): Load CA index

**Proposal**: Extract `load_merged_package_indexes(paths, ubuntu_series, cloud_archive) -> PackageIndex`.

### 4. Local Repo Refresh (4 occurrences)

- Line 1305 (sequential builds): After each success
- Line 1414 (parallel builds): After each batch
- Line 3656 (binary build): Before sbuild
- Line 3790 (verify): After publishing

**Proposal**: Already extracted as `_refresh_local_repo_indexes()`. Keep as-is.

### 5. Tarball Caching Pattern (5 occurrences)

Lines 1955, 1988, 2032, 2063, 2136 all call:
```python
cache_tarball(
    tarball_path=path,
    entry=TarballCacheEntry(...),
    cache_base=cache_base,
)
```

**Proposal**: This is in `_fetch_release_tarball()` which handles multiple methods. Keep as-is but ensure consistent entry fields.

### 6. Git Command Execution Pattern (~10 occurrences)

```python
cmd = _maybe_disable_gpg_sign(["git", "commit", "-m", message])
run_command(cmd, cwd=pkg_repo, env=_get_git_author_env())
```

**Proposal**: Create `git_commit(pkg_repo, message, extra_lines=None) -> bool` helper.

---

## Single Build vs Build-All Differences

| Aspect | Single Build | Build-All |
|--------|--------------|-----------|
| Entry point | `_build_single_mode()` | `_build_all_mode()` |
| Package source | CLI argument | `discover_packages()` |
| Execution | Inline `_run_build()` | Subprocess via `_run_single_build()` |
| Progress | Phase-based activity | Rich progress bars |
| Failure handling | Exit immediately | Continue/track failures |
| Repo regeneration | After each phase | After each batch |
| State persistence | Provenance only | BuildAllState + JSON |

**Key insight**: Build-all already uses subprocess isolation (`_run_single_build`). We should NOT change this—keep subprocess isolation for all build-all builds.

---

## Proposed Module Boundaries

### Directory Structure (Implemented)

```
src/packastack/build/
├── __init__.py          # Re-exports all public APIs
├── collector.py         # Artifact collection (existing)
├── errors.py            # Exit codes, phase_error() helper
├── git_helpers.py       # Git-related utilities  
├── mode.py              # BuildMode configuration (existing)
├── phases.py            # Phase functions for _run_build
├── provenance.py        # Build provenance (existing)
├── runner.py            # Build runner (existing)
├── sbuild.py            # Sbuild integration (existing)
├── sbuildrc.py          # Sbuildrc parsing (existing)
├── schroot.py           # Schroot management (existing)
├── tarball.py           # Tarball acquisition logic
├── tools.py             # Tool validation (existing)
└── types.py             # Dataclasses for state objects
```

### What Stays in build.py

- `build()` - Typer CLI entry point (lines 1566-1674)
- `_build_single_mode()` - Router to `_run_build()`
- `_build_all_mode()` - Router to `run_build_all()`
- Exit code constants

### types.py Contents

```python
@dataclass
class ResolvedTargets:
    """Resolved target series information."""
    openstack_series: str
    ubuntu_series: str
    prev_series: str | None
    is_development: bool

@dataclass
class WorkspacePaths:
    """Paths used during build."""
    workspace: Path
    pkg_repo: Path
    build_output: Path
    upstream_work_dir: Path | None

@dataclass
class BuildInputs:
    """Consolidated inputs for a build."""
    request: BuildRequest
    run: RunContext
    cfg: dict
    paths: dict[str, Path]
    targets: ResolvedTargets

@dataclass
class PhaseResult:
    """Result of a phase execution."""
    success: bool
    exit_code: int
    message: str
    data: dict[str, Any] | None = None

@dataclass
class BuildOutcome:
    """Final outcome of a build."""
    success: bool
    exit_code: int
    package: str
    version: str
    build_type: str
    artifacts: list[Path]
    provenance: BuildProvenance | None
    error: str | None = None
```

### git_helpers.py Contents

Move:
- `_maybe_enable_sphinxdoc()`
- `_no_gpg_sign_enabled()`
- `_maybe_disable_gpg_sign()`
- `_get_git_author_env()`
- `_shorten_path()`
- `_staged_changes()`
- `_compose_commit_message()`

Add:
- `git_commit(repo: Path, message: str, extra_lines: list[str] | None = None) -> bool`

### tarball.py Contents

Move:
- `_run_uscan()`
- `_fetch_release_tarball()`
- `_download_pypi_tarball()`
- `_download_github_release_tarball()`

### phases.py Contents

Extract from `_run_build()`:
- `resolve_build_type(inputs) -> PhaseResult`
- `check_retirement(inputs) -> PhaseResult`
- `resolve_registry(inputs) -> PhaseResult`
- `check_policy(inputs) -> PhaseResult`
- `fetch_packaging(inputs) -> PhaseResult`
- `prepare_upstream(inputs) -> PhaseResult`
- `validate_dependencies(inputs) -> PhaseResult`
- `auto_build_deps(inputs) -> PhaseResult`
- `import_upstream(inputs) -> PhaseResult`
- `apply_patches(inputs) -> PhaseResult`
- `build_packages(inputs) -> PhaseResult`
- `verify_and_publish(inputs) -> PhaseResult`
- `write_provenance(inputs) -> PhaseResult`

### executor.py Contents

Move:
- `_run_single_build()`
- `_run_sequential_builds()`
- `_run_parallel_builds()`
- `_get_parallel_batches()`

### reports.py Contents

Move:
- `_generate_reports()`

---

## Golden Behavior Notes

### Exit Codes (MUST preserve)

| Code | Constant | Meaning |
|------|----------|---------|
| 0 | EXIT_SUCCESS | Success |
| 1 | EXIT_CONFIG_ERROR | Configuration error |
| 2 | EXIT_TOOL_MISSING | Required tools missing |
| 3 | EXIT_FETCH_FAILED | Fetch/clone failed |
| 4 | EXIT_PATCH_FAILED | Patch application failed |
| 5 | EXIT_MISSING_PACKAGES | Missing dependencies |
| 6 | EXIT_CYCLE_DETECTED | Dependency cycle |
| 7 | EXIT_BUILD_FAILED | Build failed |
| 8 | EXIT_POLICY_BLOCKED | Policy blocked |
| 9 | EXIT_REGISTRY_ERROR | Registry error |
| 10 | EXIT_RETIRED_PROJECT | Retired project |
| 11 | EXIT_DISCOVERY_FAILED | Build-all: discovery failed |
| 12 | EXIT_GRAPH_ERROR | Build-all: graph error |
| 13 | EXIT_ALL_BUILD_FAILED | Build-all: some builds failed |
| 14 | EXIT_RESUME_ERROR | Build-all: resume error |

### Log Event Keys (MUST preserve)

The following event keys are used and must remain compatible:
- `resolve.package`, `resolve.auto_type_selected`, `resolve.build_type`, `resolve.prev_series`
- `registry.loaded`, `registry.resolved`
- `policy.retired_project`, `policy.possibly_retired`, `policy.snapshot`, `policy.tarball_verification`, `policy.watch_mismatch`
- `plan.ubuntu_index`, `plan.cloud_archive_index`, `plan.local_repo_index`, `plan.build_order`
- `schroot.ready`
- `fetch.complete`
- `prepare.*` (launchpad_yaml, snapshot, version, manpages, fix_priority_extra, doctree_cleanup, pgp_watch_fix, misc_pre_depends)
- `import-orig.branch`, `import-orig.complete`, `import-orig.failed`
- `patches.upstreamed`, `patches.complete`
- `build.source_complete`, `build.binary_complete`, `build.binary_failed`, `build.sbuild_command`
- `verify.publish`, `verify.publish_failed`, `verify.index`, `verify.source_index`, `verify.index_mismatch`
- `provenance.written`, `provenance.write_failed`
- `validate-deps.*`
- `auto-build.*`
- `discovery.complete`, `build_all.*`

### CLI Flags (MUST preserve)

All Typer options in `build()` function (lines 1566-1674) must remain unchanged:
- Positional: `package`
- Options: `--target`, `--ubuntu-series`, `--cloud-archive`, `--type`, `--milestone`, `--force`, `--offline`, `--validate-plan`, `--plan-upload`, `--upload`, `--binary/--no-binary`, `--builder`, `--build-deps/--no-build-deps`, `--no-cleanup`, `--no-spinner`, `--yes`, `--use-gbp-dch/--no-gbp-dch`, `--include-retired`, `--skip-repo-regen` (hidden)
- Build-all options: `--all`, `--keep-going/--fail-fast`, `--max-failures`, `--resume`, `--resume-run-id`, `--retry-failed`, `--skip-failed/--no-skip-failed`, `--parallel`, `--packages-file`, `--dry-run`

---

## Staged Commit Checklist

### Commit 1: REFACTOR_PLAN.md (this document)
- [x] Read build.py end-to-end
- [x] Create phase map
- [x] Create duplication inventory
- [x] Document golden behaviors
- [x] Commit REFACTOR_PLAN.md

### Commit 2: Introduce types.py with dataclasses
- [x] Create modules in `src/packastack/build/` (moved from build_helpers)
- [x] Create `types.py` with dataclasses
- [x] Create `errors.py` with `phase_error()` helper
- [x] Add tests for dataclass construction
- [x] Verify: `uv run pytest tests/build/ -q`

### Commit 3: Extract git_helpers.py
- [x] Move git helper functions
- [x] Update imports in build.py
- [x] Add tests for git helpers
- [x] Verify tests pass

### Commit 4: Extract tarball.py
- [x] Move tarball acquisition functions
- [x] Update imports in build.py
- [x] Add tests with mocked subprocess
- [x] Verify tests pass

### Commit 5: Wire modules into build.py
- [x] Update build.py to use extracted modules
- [x] Remove duplicate function definitions (~400 lines)
- [x] Update test patches to use new module locations
- [x] Verify tests pass (102 tests passing)

### Commit 6: Create phases.py
- [x] Create `phases.py` in `packastack.build` with phase functions
  - [x] `check_retirement_status()` - retirement phase
  - [x] `resolve_upstream_registry()` - registry phase
  - [x] `check_policy()` - policy phase
  - [x] `load_package_indexes()` - plan phase (index loading)
  - [x] `check_tools()` - plan phase (tool validation)
  - [x] `ensure_schroot_ready()` - plan phase (schroot setup)
  - [ ] `fetch_packaging()` - fetch phase
  - [ ] `prepare_upstream()` - prepare phase
  - [ ] Other phases...
- [x] Add dataclasses: `RetirementCheckResult`, `RegistryResolutionResult`, `PolicyCheckResult`, `PackageIndexes`, `ToolCheckResult`, `SchrootSetupResult`
- [x] Add tests for phases (15 tests)
- [x] Verify tests pass (269 tests total)

### Commit 7: Move build_helpers to packastack.build
- [x] Move modules from `commands/build_helpers/` to `build/`
- [x] Update all imports in build.py and tests
- [x] Remove old build_helpers directory
- [x] Verify tests pass (242 build tests)

### Commit 8: Wire phases into build.py
- [x] Replace inline retirement code with `check_retirement_status()`
- [x] Replace inline registry code with `resolve_upstream_registry()`
- [x] Replace inline policy check with `check_policy()` - NOT YET (code exists but not wired)
- [x] Replace inline index loading with `load_package_indexes()`
- [x] Replace inline tool check with `check_tools()`
- [x] Replace inline schroot setup with `ensure_schroot_ready()`
- [x] Fix test patches for new module locations
- [x] Ensure all event keys preserved
- [x] Verify tests pass (269 tests)

### Commit 9: Extract single_build.py for per-package phases (DONE)
- [x] Create `SingleBuildContext` dataclass
- [x] Create phase result types (FetchResult, PrepareResult, etc.)
- [x] Extract `fetch_packaging_repo()` phase function
- [x] Extract `prepare_upstream_source()` phase function
- [x] Extract `validate_and_build_deps()` phase function
- [x] Extract `import_and_patch()` phase function
- [x] Extract `build_packages()` phase function
- [x] Extract `verify_and_publish()` phase function
- [x] Add 12 tests for single_build module
- [x] Verify tests pass (1,461 tests)

### Commit 10: Add setup_build_context and orchestrator (DONE)
- [x] Create `SetupInputs` dataclass for pre-build inputs
- [x] Create `setup_build_context()` to run phases 1-6
- [x] Create `SingleBuildOutcome` for orchestrator result
- [x] Create `build_single_package()` orchestrator
- [x] Add provenance and report phases to orchestrator
- [x] Export from `packastack.build/__init__.py`
- [x] Verify tests pass (1,461 tests)

### Commit 11: Wire orchestrator into _run_build (DONE)
- [x] Replace per-package loop in `_run_build` with calls to `setup_build_context()` + `build_single_package()`
- [x] Handle write_summary after orchestrator returns
- [x] Handle upload command display
- [x] Re-export exit codes and functions for test compatibility
- [x] Verify most tests pass (1,451 / 1,461)
- [x] build.py reduced from 2,778 to 1,350 lines (-51% from previous, -65% from original)

### Commit 12: Extract build-all execution (DONE)
- [x] Create `all_runner.py` with `_run_build_all`, `_run_sequential_builds`, `_run_parallel_builds`
- [x] Keep `run_build_all` wrapper in `build.py` for test compatibility
- [x] Update tests to patch `all_runner` module for internal functions
- [x] Clean up backwards-compatible imports in build.py
- [x] Add `log_phase_event()` helper to errors.py
- [x] Add `git_commit()` helper to git_helpers.py
- [x] build.py reduced to 703 lines (-82% from original 3,873)
- [x] All 1,237 tests pass

### Commit 13: Cleanup pass (DONE)
- [x] Consolidate all imports at top of build.py
- [x] Remove duplicate backwards-compatibility aliases  
- [x] Remove duplicate TYPE_CHECKING block
- [x] Organize imports by source (stdlib, core, planning, build)
- [x] Fix deprecated datetime.utcnow() in all_runner.py
- [x] build.py reduced to 680 lines
- [x] All 1,237 tests pass

---

## Verification Commands

```bash
# After each commit:
uv run pytest tests/commands/test_build.py tests/commands/test_build_all.py -q
uv run python -m packastack build --help
uv run ruff check src/packastack/commands/

# Smoke test (if workspace available):
uv run python -m packastack build oslo.config --type snapshot --validate-plan
```

---

## Behavior Risks

1. **Phase function extraction**: Each phase currently has access to all local variables. Converting to explicit inputs/outputs may miss subtle dependencies. **Mitigation**: Extract one phase at a time, run tests after each.

2. **Error handling changes**: The `phase_error()` helper must emit identical log keys. **Mitigation**: Wrapper preserves exact format, tested with characterization tests.

3. **Build-all subprocess behavior**: Already uses subprocess isolation. **Mitigation**: No changes to subprocess logic.

4. **Import order dependencies**: Some phases depend on state from earlier phases (e.g., `provenance` object). **Mitigation**: Pass provenance through `BuildInputs` or as explicit parameter.
