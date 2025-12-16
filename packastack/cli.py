# Copyright (C) 2025 Canonical Ltd
#
# License granted by Canonical Limited
#
# SPDX-License-Identifier: GPL-3.0-only
#
# This file is part of PackaStack. See LICENSE for details.

"""CLI entry point for packastack using cliff."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Sequence

from cliff.app import App
from cliff.commandmanager import CommandManager
from oslo_config import cfg

from packastack.logging_setup import _setup_cli_logging


class PackastackCommandManager(CommandManager):
    """Command manager that registers PackaStack commands."""

    def __init__(self) -> None:
        super().__init__(namespace="packastack.commands")
        # Import commands lazily to avoid import cycles
        from packastack.cmds.import_tarballs import ImportTarballsCommand

        self.add_command("import", ImportTarballsCommand)


def add_opts_to_parser(parser: argparse.ArgumentParser, opts: list[cfg.Opt]) -> None:
    """Add oslo.config options to an argparse parser."""

    for opt in opts:
        names: list[str] = []

        if opt.short:
            names.append(f"-{opt.short}")

        option_name = opt.name.replace("_", "-")
        if opt.positional:
            names.append(option_name)
        else:
            names.append(f"--{option_name}")

        kwargs = opt._get_argparse_kwargs(None)
        if kwargs.get("default") is None:
            kwargs["default"] = opt.default

        parser.add_argument(*names, **kwargs)


class PackastackApp(App):
    """PackaStack CLI application."""

    log = logging.getLogger(__name__)

    def __init__(self, **kwargs):
        self.conf = cfg.ConfigOpts()
        self._config_registered = False
        self._registered_command_opts: dict[str, list[cfg.Opt]] = {}
        self.cli_description = "PackaStack - OpenStack packaging management tool."
        self.cli_version = None
        super().__init__(
            description=self.cli_description,
            version=self.cli_version,
            command_manager=PackastackCommandManager(),
            deferred_help=True,
            **kwargs,
        )

    def _register_config_options(self) -> None:
        if self._config_registered:
            return

        global_root_opt = cfg.StrOpt(
            "root",
            default=None,
            help=(
                "Root directory to operate in (default: current working "
                "directory)"
            ),
        )

        self.conf.register_cli_opt(global_root_opt)
        self._register_command_options()

        self.conf.register_cli_opt(
            cfg.SubCommandOpt(
                "command",
                title="Commands",
                description="Available PackaStack commands.",
                handler=self._add_subcommands,
            )
        )

        self._config_registered = True

    def _register_command_options(self) -> None:
        for name, command_ep in self.command_manager:
            command_class = command_ep.load()
            opts: list[cfg.Opt] = getattr(command_class, "cli_opts", [])
            if opts:
                self.conf.register_cli_opts(opts)
                self._registered_command_opts[name] = opts

    def _add_subcommands(self, subparsers) -> None:
        for name, command_ep in self.command_manager:
            command_class = command_ep.load()
            command = command_class(self, None)
            base_parser = command.get_parser(name)

            parser = subparsers.add_parser(
                name,
                add_help=False,
                description=base_parser.description,
            )

            opts = self._registered_command_opts.get(name, [])
            add_opts_to_parser(parser, opts)

            parser.set_defaults(command=name, __command_class=command_class)

    def _build_parsed_args(self) -> argparse.Namespace:
        values = {
            key: value
            for key, value in vars(self.conf._namespace).items()
            if not key.startswith("_") or key == "__command_class"
        }
        return argparse.Namespace(**values)

    def initialize_app(self, argv: list[str]) -> None:  # noqa: D401
        """Configure logging for CLI execution."""

        try:
            root_value = Path(self.options.root) if self.options.root else None
            _setup_cli_logging(root_value)
        except Exception as exc:  # pragma: no cover - defensive logging
            self.log.warning("Failed to configure CLI logging: %s", exc)

    def run(self, argv: Sequence[str] | None = None):  # noqa: D401
        """Parse arguments with oslo.config and dispatch commands."""

        self._register_config_options()
        arg_list = list(argv) if argv is not None else None

        try:
            self.conf(
                arg_list,
                project="packastack",
                prog="packastack",
                description=self.cli_description,
                version=self.cli_version,
            )
        except SystemExit as exc:
            return exc.code

        self.options = self._build_parsed_args()
        self.initialize_app(arg_list or [])

        if not getattr(self.options, "command", None):
            self.conf.print_help()
            return 0

        command_class = getattr(self.options, "__command_class", None)
        if command_class is None:
            cmd_factory, cmd_name, _ = self.command_manager.find_command(
                [self.options.command]
            )
            command_class = cmd_factory

        cmd = command_class(self, None)
        result = cmd.run(self.options)
        return self.clean_up(cmd, result, err=None) or 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the PackaStack CLI application."""

    app = PackastackApp()
    return app.run(list(argv) if argv is not None else None)


if __name__ == "__main__":
    sys.exit(main())
