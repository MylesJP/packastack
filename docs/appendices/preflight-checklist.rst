Preflight Checklist
===================

A quick, opinionated preflight before you push the big red button. Run through this list to avoid the classic “oh no” moments.

- Workspace exists and is tidy (no half-baked caches you meant to delete).
- ``packastack init`` has been run in this workspace; ``~/.config/packastack/config.yaml`` looks sensible.
- ``openstack/releases`` cache is present and recent enough for your tolerance.
- Schroot list matches your target series/arch; no ghosts: ``schroot --list | grep packastack``.
- Local repo present at ``<workspace>/localrepo`` with writable perms.
- Network plan chosen: online with working DNS, or truly offline with caches seeded.
- Disk space check: enough room for builds, logs, and repo updates (schroots get hungry).
- Secrets: none in the workspace; builds should not need them. If you see secrets, put them somewhere safer.
- Tests enabled unless you have a very loud reason; surprises belong in birthday parties, not CI logs.

Last two steps: breathe, and run the command you actually intend to run (copy-paste carefully; typos are gremlins).
