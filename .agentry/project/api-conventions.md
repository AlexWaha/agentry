# API Conventions

**Date:** 2026-10-09
**Author:** technical-writer (task-0052, swarm task-0118)
**Status:** Draft
**Version:** 2.0

Agentry has no HTTP surface: no server, no routes, no request or response
bodies. There are no API conventions to record, and none are written here.

The interfaces it does have are command-line scripts under `$AGENTRY_ROOT/tools/` and
the Claude Code hook protocol. Both are listed in `stack.md` (Commands) and
`architecture.md` (Modules).

## Why this file stays

The decision for task-0052 was to keep a one-paragraph file rather than delete
it. `.claude/CLAUDE.md`, the `rules/api-conventions.md` stub, `settings.json`
and the architect, reviewer and senior-backend-dev agent files all name this
path, and a missing target would send an agent looking for it elsewhere.
If Agentry ever grows an HTTP interface, replace this file with its real
conventions.
