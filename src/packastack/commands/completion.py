# This file is part of Packastack, a tool for building OpenStack packages for Ubuntu.
#
# Copyright 2025 Canonical Ltd.
#
# SPDX-License-Identifier: GPL-3.0-only
#
# Packastack is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License version 3, as published by the
# Free Software Foundation.
#
# Packastack is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranties of MERCHANTABILITY,
# SATISFACTORY QUALITY, or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# Packastack. If not, see <http://www.gnu.org/licenses/>.

"""Implementation of `packastack completion` command.

Generate shell completion scripts for bash, zsh, and fish.
"""

from __future__ import annotations

import typer

EXIT_SUCCESS = 0


def completion(
    shell: str = typer.Argument(..., help="Shell type: bash|zsh|fish"),
) -> None:
    """Generate shell completion script.

    Installation:
        bash:  packastack completion bash >> ~/.bashrc
        zsh:   packastack completion zsh >> ~/.zshrc
        fish:  packastack completion fish > ~/.config/fish/completions/packastack.fish
    """
    shell = shell.lower()

    if shell == "bash":
        print(_bash_completion())
    elif shell == "zsh":
        print(_zsh_completion())
    elif shell == "fish":
        print(_fish_completion())
    else:
        typer.echo(f"Unsupported shell: {shell}", err=True)
        typer.echo("Supported shells: bash, zsh, fish", err=True)
        raise typer.Exit(1)


def _bash_completion() -> str:
    """Generate bash completion script."""
    return """
# PackaStack bash completion

_packastack_complete() {
    local cur prev words cword
    _init_completion || return

    # Get completions from packastack itself
    COMPREPLY=( $(compgen -W "build plan search explain init refresh clean completion" -- "$cur") )
}

complete -F _packastack_complete packastack
"""


def _zsh_completion() -> str:
    """Generate zsh completion script."""
    return """
# PackaStack zsh completion

#compdef packastack

_packastack() {
    local -a commands
    commands=(
        'build:Build packages'
        'plan:Generate build plan'
        'search:Search for targets'
        'explain:Explain target resolution'
        'init:Initialize workspace'
        'refresh:Refresh schroots'
        'clean:Clean build artifacts'
        'completion:Generate completion script'
    )

    _arguments -C \
        "1: :->cmds" \
        "*::arg:->args"

    case $state in
        cmds)
            _describe "command" commands
            ;;
        args)
            case $words[1] in
                build|plan|search|explain)
                    # Add target completion here
                    ;;
            esac
            ;;
    esac
}

_packastack
"""


def _fish_completion() -> str:
    """Generate fish completion script."""
    return """
# PackaStack fish completion

# Main commands
complete -c packastack -f -n __fish_use_subcommand -a build -d 'Build packages'
complete -c packastack -f -n __fish_use_subcommand -a plan -d 'Generate build plan'
complete -c packastack -f -n __fish_use_subcommand -a search -d 'Search for targets'
complete -c packastack -f -n __fish_use_subcommand -a explain -d 'Explain target resolution'
complete -c packastack -f -n __fish_use_subcommand -a init -d 'Initialize workspace'
complete -c packastack -f -n __fish_use_subcommand -a refresh -d 'Refresh schroots'
complete -c packastack -f -n __fish_use_subcommand -a clean -d 'Clean build artifacts'
complete -c packastack -f -n __fish_use_subcommand -a completion -d 'Generate completion script'

# Completion command
complete -c packastack -f -n '__fish_seen_subcommand_from completion' -a 'bash zsh fish'
"""
