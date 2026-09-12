#!/usr/bin/env bash
#
# Removes accidental 'nul'/'NUL' files created by Unix-style redirects
# (`>/dev/null`, `2>/dev/null`) on Windows / Git Bash setups.
#
# Harmless no-op on Linux/macOS — the files do not exist.
# Called by Pre/PostToolUse hooks on Bash events.
#

find . -maxdepth 5 -iname "nul" -type f -delete

exit 0
