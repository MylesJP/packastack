# Target Resolution Quick Reference

## Common Usage Patterns

### Exact Match
```bash
# Build a specific package
packastack build glance
packastack plan nova
packastack explain keystone
```

### Prefix Match (Shell-Safe)
```bash
# Build all packages starting with prefix
packastack build ^python-oslo --all-matches
packastack build ^glance --all-matches
```

### Contains Match
```bash
# Find all client packages
packastack search ~client

# Build all packages containing "oslo"
packastack build ~oslo --all-matches
```

### Scoped Match
```bash
# Match by source package name
packastack build source:glance

# Match by canonical upstream ID
packastack build canonical:gnocchixyz/gnocchi

# Match by deliverable name
packastack build deliverable:glance
```

## Search Examples

```bash
# Search for a target
packastack search glance

# Prefix search
packastack search ^python-

# Search in specific scope
packastack search canonical:openstack/ --scope canonical

# JSON output for scripting
packastack search ~client --format json

# Refresh completion cache
packastack search glance --refresh-cache
```

## Shell Completion

```bash
# Install completion (one-time setup)
packastack completion bash >> ~/.bashrc   # Bash
packastack completion zsh >> ~/.zshrc     # Zsh
packastack completion fish > ~/.config/fish/completions/packastack.fish  # Fish

# Reload shell
source ~/.bashrc  # or restart terminal

# Use tab completion
packastack build gla<TAB>        # Completes to glance
packastack build source:<TAB>    # Shows source packages
packastack build canonical:<TAB> # Shows canonical IDs
```

## Target Expression Syntax

| Expression | Meaning | Example |
|------------|---------|---------|
| `glance` | Exact match | `glance` |
| `^glance` | Prefix match (preferred) | `glance`, `glance-store` |
| `~client` | Contains match | `novaclient`, `python-glanceclient` |
| `'glance*'` | Glob (must quote, discouraged) | `glance`, `glance-store` |
| `source:glance` | Scoped exact | `glance` (source package) |
| `canonical:gnocchixyz/gnocchi` | Canonical ID | `gnocchi` |

## Shell Safety Warning

**❌ BAD** (shell expands before PackaStack sees it):
```bash
packastack build glance*
# Shell expands to: packastack build glance glance-store glance-ui
```

**✅ GOOD** (shell-safe):
```bash
packastack build ^glance --all-matches
# OR quote it:
packastack build 'glance*' --all-matches
```

PackaStack will warn you if it detects suspected shell expansion.

## Canonical Identifiers

Every project in `upstreams.yaml` must have a canonical ID:

```yaml
projects:
  gnocchi:
    canonical: gnocchixyz/gnocchi  # Required
    common_names: [gnocchi]
    upstream:
      type: git
      host: github
      url: https://github.com/gnocchixyz/gnocchi.git
```

For OpenStack projects not in the registry, the canonical ID is inferred as `openstack/<project>`.

## Resolution Order

PackaStack resolves targets in this order:

1. **Exact source package** name
2. **Exact canonical** upstream ID
3. **Exact deliverable** name (OpenStack projects)
4. **Exact alias** from registry
5. **Prefix** match (if `^` or `*`)
6. **Contains** match (if `~`)

First tier with exactly one match wins.

## Ambiguity Handling

Multiple matches require explicit selection:

```bash
# This is ambiguous if multiple matches:
$ packastack build ^glance
[resolve] Error: Ambiguous target '^glance' matched 3 packages
Use --all-matches to build all, or specify one

# Solution 1: Build all
$ packastack build ^glance --all-matches

# Solution 2: Be specific
$ packastack build source:glance
```

## Flags

- `--all-matches`: Build/plan all matched targets (for prefix/contains)
- `--format json`: JSON output (search command)
- `--scope <scope>`: Restrict search scope
- `--refresh-cache`: Update completion cache
