# Copyright (C) 2025 Canonical Ltd
#
# License granted by Canonical Limited
#
# SPDX-License-Identifier: GPL-3.0-only
#
# This file is part of PackaStack. See LICENSE for details.

"""Configure command for setting packaging round and LP bugs."""

from cliff.command import Command

from packastack.config import PackastackConfig


class ConfigureCommand(Command):
    """Configure packaging round and Launchpad bug numbers."""

    def take_action(self, parsed_args):
        """Execute the configure command."""
        config = PackastackConfig()

        self.app.stdout.write("\nPackaStack Configuration\n")
        self.app.stdout.write("=" * 70 + "\n\n")

        # Get packaging round
        current_round = config.get_packaging_round()
        if current_round:
            self.app.stdout.write(
                f"Current packaging round: {current_round}\n"
            )
        else:
            self.app.stdout.write("No packaging round configured.\n")

        # Get Ubuntu release
        current_ubuntu = config.get_ubuntu_release()
        if current_ubuntu:
            self.app.stdout.write(
                f"Current Ubuntu release: {current_ubuntu}\n\n"
            )
        else:
            self.app.stdout.write("No Ubuntu release configured.\n\n")

        round_name = input(
            "Enter packaging round (milestone-2/milestone-3/rc1/final) "
            "[leave blank to keep current]: "
        ).strip()

        if round_name:
            config.set_packaging_round(round_name)
            self.app.stdout.write(f"Set packaging round to: {round_name}\n")
        elif not current_round:
            self.app.stdout.write(
                "WARNING: No packaging round set. "
                "You will be reminded to configure this.\n"
            )

        ubuntu_release = input(
            "Enter Ubuntu release (e.g., questing, resolute) "
            "[leave blank to keep current]: "
        ).strip()

        if ubuntu_release:
            config.set_ubuntu_release(ubuntu_release)
            self.app.stdout.write(f"Set Ubuntu release to: {ubuntu_release}\n\n")
        elif not current_ubuntu:
            self.app.stdout.write(
                "WARNING: No Ubuntu release set. "
                "You will be reminded to configure this.\n\n"
            )

        # Get LP bug numbers
        self.app.stdout.write("Launchpad Bug Numbers\n")
        self.app.stdout.write("-" * 70 + "\n")

        milestones = ["milestone-2", "milestone-3", "rc1", "final"]
        current_bugs = config.get_all_lp_bugs()

        for milestone in milestones:
            current_bug = current_bugs.get(milestone, "not set")
            bug_input = input(
                f"  {milestone:12s} (current: {current_bug}): "
            ).strip()

            if bug_input:
                # Remove common prefixes if user includes them
                bug_input = bug_input.replace("LP:", "").replace("#", "").strip()
                config.set_lp_bug(milestone, bug_input)
                self.app.stdout.write(
                    f"    → Set {milestone} bug to LP: #{bug_input}\n"
                )

        self.app.stdout.write("\n" + "=" * 70 + "\n")
        self.app.stdout.write("Configuration saved to ./packastack/config.json\n")

        # Show summary
        self.app.stdout.write("\nCurrent Configuration:\n")
        self.app.stdout.write(f"  Packaging round: {config.get_packaging_round()}\n")
        self.app.stdout.write(f"  Ubuntu release: {config.get_ubuntu_release()}\n")
        self.app.stdout.write("  LP Bugs:\n")
        for milestone in milestones:
            bug = config.get_lp_bug(milestone)
            if bug:
                self.app.stdout.write(f"    {milestone:12s}: LP: #{bug}\n")
        self.app.stdout.write("\n")
