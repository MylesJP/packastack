## Purpose

PackaStack requires a deterministic and auditable way to determine the upstream source of truth for packages in the Ubuntu OpenStack packaging set.

Most OpenStack packages follow a uniform model: upstream hosted on OpenDev, releases governed by the `openstack/releases` repository, and tarballs published on `tarballs.opendev.org`. However, this is not universally true. Some packages:

- are hosted outside OpenDev (for example on GitHub)
- are not governed by OpenStack releases
- derive releases from git tags or GitHub Releases instead of OpenStack tarballs
- cannot reasonably support detached tarball signatures

The Upstreams Registry exists to encode these exceptions explicitly.

It is a canonical, in-tree configuration file that defines only where the default model does not apply.

## Design principles

1. Defaults first
   - The vast majority of projects are not listed.
   - If a project follows standard OpenStack rules, it is omitted.

2. Exceptions are explicit
   - Every listed project must justify its presence.
   - If a project is listed, PackaStack must behave differently.

3. Registry, not convenience
   - The registry defines upstream resolution rules.
   - It does not exist to model CLI aliases or naming ergonomics.

4. Auditable and reproducible
   - Every build records exactly how upstream resolution occurred.
   - A build can be inspected or recreated later.

5. Fail rather than guess
   - If a project is neither in the registry nor governed by `openstack/releases`, PackaStack must fail clearly and require human intervention.

## File locations

### Canonical registry (owned by PackaStack)

`packastack/data/upstreams.yaml`

This file is part of the source tree and reviewed like code.

### Optional local override (user-owned)

`~/.config/packastack/upstreams.yaml`

Overrides are merged on top of the canonical registry and must be reported.

## Merge semantics

1. Load `packastack/data/upstreams.yaml`.
2. If `~/.config/packastack/upstreams.yaml` exists, load it.
3. Schema versions must match exactly.
4. Merge rules:
   - scalar values: override replaces base
   - mappings: override replaces keys it defines
   - lists: override replaces entire list
   - unknown keys: warn (non-fatal)

The merged result is the effective operational registry.

## Schema (v1)

### Top-level keys

- `version` (integer, required)
- `defaults` (mapping, required)
- `projects` (mapping, optional)

## defaults

Defines the standard OpenStack behavior assumed for all projects not listed.

Typical defaults:

- upstream hosted on `https://opendev.org/openstack/<deliverable>`
- releases governed by `openstack/releases`
- tarballs from `tarballs.opendev.org`
- detached `.asc` signatures verified when available
- requirements extracted from standard Python metadata

Supported keys:

- `upstream`
- `release_source`
- `tarball`
- `signatures`
- `requirements`

Defaults must be applied before project validation.

## projects

A mapping of exceptional projects only.

A project must be listed if any of the following are true:

- upstream source is not derivable from OpenDev defaults
- release discovery is not governed by `openstack/releases`
- tarballs do not come from OpenStack tarballs
- verification model differs from detached OpenStack signatures

If none apply, the project must not be listed.

## Project entry schema

### upstream (required)

Defines where upstream source lives.

- `type`: `git`
- `host`: `opendev | github | gitlab | other` (informational)
- `url`: git clone URL
- `default_branch`: branch name

### release_source (required)

Defines how releases are discovered.

Supported types:

- `openstack_releases`
  - `deliverable`
  - `strict` (boolean)

- `git_tags`
  - `tag_regex` (must include `(?P<version>...)`)
  - `strict` (boolean)

- `pypi`
  - `project`
  - `strict` (boolean)

- `pinned`
  - `ref`
  - `strict` (boolean)

### tarball

Defines how orig tarballs are obtained.

- `prefer`: ordered list of:
  - `official`
  - `github_release`
  - `pypi`
  - `git_archive`

The first viable method must be used.

### signatures

Defines verification policy.

- `mode`:
  - `auto`
  - `required_detached`
  - `git_tag`
  - `git_commit`
  - `none`

If verification is not applicable, PackaStack must remove `debian/upstream` signature material for that build.

### requirements

Defines how upstream dependencies are extracted.

- `files`: ordered list (for example `pyproject.toml`, `requirements.txt`)
- `optional_files`
- `include_optional_extras` (boolean)

Upstream requirements are authoritative.

### watch (optional, advisory)

Defines expected `debian/watch` intent. Used only to emit warnings on likely mismatch.

- `expect.mode`: `openstack_tarball | github_release | pypi | git_tags | custom`
- `expect.base_url`: optional

Mismatch detection must never fail the build.

## Failure rules

PackaStack must fail with a clear error if:

- a project is not listed in the registry, and
- it is not governed by `openstack/releases`

This prevents silent guessing and drift.

## Provenance requirement

Every build must record:

- whether resolution came from defaults or an explicit registry entry
- resolved upstream URL and ref
- release_source type and details
- tarball method used
- verification mode and result
- any override applied
- any watch mismatch warning

This record enables audit and recreation.

## Ownership model

- `upstreams.yaml` is treated as an ABI-level contract
- changes are reviewed deliberately
- new entries must justify why defaults are insufficient

## Future work

- explicit dependency-name mapping tables
- per-Ubuntu-series overrides
- fork selection support
