#!/usr/bin/env bash
#
# Removes accidental 'nul'/'NUL' files left by output redirects on Windows /
# Git Bash setups. Measured 2026-09-14: the `cmd.exe` spelling (`>NUL`,
# `2>NUL`) is what leaves them - bash has no device named NUL, so it opens a
# file of that name. The Unix spelling (`2>/dev/null`) does NOT: MSYS2 mounts a
# real device there. It stays forbidden by policy, not because of this file.
# Deleting must happen from bash: Python's os.unlink cannot remove a NUL entry.
# See .claude/rules/quality-standard.md for the measurement.
#
# Harmless no-op on Linux/macOS - the files do not exist.
# Called by Pre/PostToolUse hooks on Bash events.
#

find . -maxdepth 5 -iname "nul" -type f -delete

exit 0
