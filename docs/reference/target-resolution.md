# Target Resolution System

## Overview

PackaStack implements a deterministic, shell-safe target resolution system for identifying and matching build targets. The system provides:

- **Explicit grammar** for target expressions
- **Shell-safe syntax** avoiding glob expansion pitfalls
- **Canonical upstream identifiers** for all projects
- **Tab completion** support via local cache
- **Deterministic resolution** with clear tier-based matching

## Target Expression Grammar

### Basic Syntax

```
TargetExpr := [<scope>:]<body>
```

Where:
- `<scope>` is optional: `source`, `canonical`, `upstream`, `deliverable`, or `repo`
- `<body>` is the match expression

### Match Modes

#### 1. Exact Match (default)

```bash
packastack build glance
```

Matches exactly `glance` (case-insensitive).

#### 2. Prefix Match (shell-safe)

```bash
packastack build ^glance
```

Matches any target starting with `glance` (e.g., `glance`, `glance-store`).

**Preferred over glob syntax.**

#### 3. Contains Match (shell-safe)

```bash
packastack build ~client
```

Matches any target containing `client` (e.g., `python-glanceclient`, `novaclient`).

#### 4. Glob-Style Prefix (discouraged)

```bash
packastack build 'glance*'
```

**MUST be quoted** to prevent shell expansion. Equivalent to `^glance`.

**Use `^glance` instead** for shell-safe operation.

### Scoped Expressions

Restrict matching to specific identifier types:

```bash
# Match by source package name
packastack build source:glance

# Match by canonical upstream ID
packastack build canonical:gnocchixyz/gnocchi

# Match by deliverable name (OpenStack governed projects)
packastack build deliverable:glance

# Match by repository namespace
packastack build repo:openstack/glance
```

Scopes reduce ambiguity and are recommended for automation.

## Resolution Tiers

Resolution proceeds through strict tiers, stopping at the first tier with exactly one match:

1. **Exact downstream source package** (`glance`)
2. **Exact canonical upstream** (`openstack/glance`, `gnocchixyz/gnocchi`)
3. **Exact deliverable/common name** (OpenStack governed projects only)
4. **Exact alias** (alternative names from registry)
5. **Prefix match** (if `^` or `*` used)
6. **Contains match** (if `~` used)

### Examples

```bash
# Tier 1: Exact source package
$ packastack build glance
# → Matches source package "glance" immediately

# Tier 2: Exact canonical
$ packastack build gnocchixyz/gnocchi
# → Matches canonical upstream ID

# Tier 5: Prefix
$ packastack build ^python-
# → Matches all packages starting with "python-"
# → Requires --all-matches if multiple matches
```

## Canonical Upstream Identifiers

Every project has a canonical upstream identifier in `upstreams.yaml`:

```yaml
projects:
  gnocchi:
    canonical: gnocchixyz/gnocchi
    common_names: [gnocchi]
    upstream:
      type: git
      host: github
      url: https://github.com/gnocchixyz/gnocchi.git
```

### Inference

For projects not explicitly listed:
- OpenStack projects: inferred as `openstack/<project>`
- Non-OpenStack: **must** have explicit entry

### Benefits

- **Unambiguous identification** across repositories
- **Retirement tracking** via canonical ID
- **Provenance** in reports and logs

## Shell Glob Safety

### The Problem

Shell glob expansion can cause unexpected behavior:

```bash
# BAD: Shell expands glance* before PackaStack sees it
$ packastack build glance*
# → Shell expands to: packastack build glance glance-store glance-ui
```

### Detection

PackaStack detects suspected shell expansion:

```
[resolve] Warning: multiple targets detected: glance glance-store
[resolve] This may be shell expansion of 'glance*'
[resolve] Prefer: ^glance  (shell-safe) or quote patterns: 'glance*'
```

### Recommendation

**Always use `^` for prefix matching:**

```bash
# GOOD: Shell-safe prefix
$ packastack build ^glance

# ACCEPTABLE: Quoted glob
$ packastack build 'glance*'
```

## Batch Selection

Use `--all-matches` to build/plan all matched targets:

```bash
# Build all packages starting with "python-"
$ packastack build ^python- --all-matches

# Plan all packages containing "client"
$ packastack plan ~client --all-matches
```

Without `--all-matches`, prefix/contains matches are **ambiguous** and require selection.

## Search Command

Discover targets without building:

```bash
# Search for targets
$ packastack search glance

# Prefix search
$ packastack search ^glance

# Scoped search
$ packastack search canonical:gnocchixyz/gnocchi

# JSON output
$ packastack search ~client --format json
```

Output shows:
- Source package name
- Canonical upstream ID
- Deliverable name (if governed)
- Kind (service, library, client, plugin)
- Governance status
- Origin (upstreams.yaml, openstack/releases, etc.)

## Tab Completion

### Setup

Generate completion script for your shell:

```bash
# Bash
$ packastack completion bash >> ~/.bashrc

# Zsh
$ packastack completion zsh >> ~/.zshrc

# Fish
$ packastack completion fish > ~/.config/fish/completions/packastack.fish
```

### Usage

Tab completion suggests:

- **Scopes** (`source:`, `canonical:`, etc.)
- **Source packages** from local cache
- **Canonical IDs** from registry
- **Deliverables** for governed projects

Completion is **fast** (<50ms) and **offline** (no network).

### Cache Refresh

Update completion cache after adding new projects:

```bash
$ packastack search glance --refresh-cache
```

Cache location: `~/.cache/packastack/completion/index.json`

## Provenance and Reporting

All resolutions are tracked:

```json
{
  "target": {
    "raw_input": "^glance",
    "parsed": {
      "scope": null,
      "match_mode": "prefix",
      "identifier": "glance"
    },
    "resolved": {
      "source_package": "glance",
      "canonical_upstream": "openstack/glance",
      "resolution_tier": 1,
      "origin": "upstreams.yaml"
    }
  }
}
```

Reports include:
- Raw user input
- Parsed expression
- Match mode and tier
- Canonical upstream ID
- Shell expansion warning (if applicable)

## Best Practices

### For Interactive Use

```bash
# Exact match when you know the name
$ packastack build glance

# Prefix for exploration
$ packastack search ^python-oslo

# Scoped for precision
$ packastack build canonical:gnocchixyz/gnocchi
```

### For Automation/CI

```bash
# Always use scoped expressions
$ packastack build source:glance

# Use canonical IDs for unambiguous identification
$ packastack build canonical:openstack/glance

# Avoid relying on --force for ambiguity (use --all-matches instead)
$ packastack build ^python-oslo --all-matches
```

### In upstreams.yaml

```yaml
projects:
  myproject:
    # REQUIRED: Canonical upstream identifier
    canonical: myorg/myproject
    
    # Common names for matching
    common_names: [myproject, my-project]
    
    # Upstream configuration
    upstream:
      type: git
      host: github
      url: https://github.com/myorg/myproject.git
```

## Migration from Old System

Old syntax (discouraged):

```bash
# Old: relied on shell glob or fuzzy matching
$ packastack build 'glance*'
$ packastack build glance --force
```

New syntax (recommended):

```bash
# New: explicit prefix, shell-safe
$ packastack build ^glance --all-matches

# New: exact with scope for automation
$ packastack build source:glance
```

## Error Messages

### Ambiguous Match

```
[resolve] Error: Ambiguous target 'glance' matched 3 packages:
  - glance
  - glance-store
  - python-glanceclient

Use --all-matches to build all, or specify one:
  packastack build ^glance --all-matches
  packastack build source:glance
```

### No Match

```
[resolve] Error: No matches for target 'nonexistent'

Suggestions:
  - Check spelling
  - Use search: packastack search ~nonexistent
  - Add to upstreams.yaml if non-OpenStack project
```

### Invalid Syntax

```
[resolve] Error: Invalid target expression 'glance@ubuntu'
Only [A-Za-z0-9._+-/] allowed in identifiers
```

## Implementation Details

### Identifier Rules

- **Allowed characters**: `A-Z`, `a-z`, `0-9`, `.`, `_`, `+`, `-`, `/`
- **Case-insensitive** for matching
- **Case-preserving** for display
- **Must not be empty**

### Scope Behavior

| Scope | Matches Against |
|-------|----------------|
| `source` | Source package names |
| `canonical` | Canonical upstream IDs |
| `upstream` | Upstream repository identifiers |
| `deliverable` | OpenStack deliverable names (governed projects only) |
| `repo` | Repository namespace/org |

### Performance

- **Completion**: <50ms typical (local cache)
- **Resolution**: <100ms typical (in-memory registry)
- **No network access** required for resolution

## See Also

- [CLI Reference](reference/cli.rst)
- [Upstreams Registry](reference/upstreams.rst)
- [Build Planning](explanation/building-packages.rst)
