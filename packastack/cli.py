# Copyright (C) 2025 Canonical Ltd
#
# License granted by Canonical Limited
#
# SPDX-License-Identifier: GPL-3.0-only
#
# This file is part of PackaStack. See LICENSE for details.

"""CLI entry point for packastack."""

import logging
from pathlib import Path

import click

from packastack.logging_setup import _setup_cli_logging


@click.group()
@click.option(
    "--root",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Root directory to operate in (default: current working directory)",
)
@click.pass_context
def cli(ctx, root: Path | None):
    """PackaStack - OpenStack packaging management tool."""
    # Persist root on context for subcommands to access
    ctx.ensure_object(dict)
    ctx.obj["root"] = root
    # Setup CLI logging to use provided root. Failures here should not
    # cause the entire CLI to crash; log and continue.
    try:
        _setup_cli_logging(root)
    except Exception as e:
        logging.getLogger(__name__).warning("Failed to configure CLI logging: %s", e)


# Setup a default logging handler for CLI commands so we capture logs
# even before subcommands configure per-run logging.
# NOTE: CLI logging setup is implemented in `packastack.logging_setup._setup_cli_logging`.


# NOTE: Do not initialize CLI logging at import time; we configure logging
# when the `cli` group is invoked so we can honor the provided `--root`.


# Import commands after cli group is defined to avoid circular imports
from packastack.cmds.import_tarballs import import_cmd  # noqa: E402

cli.add_command(import_cmd)


if __name__ == "__main__":
    cli()
