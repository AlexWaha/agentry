# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-10-16

First public release. 0.1.0 was the version on `main` before the first release
(never tagged).

The LICENSE holder, the plugin author in `plugin.json` and the marketplace owner in
`marketplace.json` are all "Oleksandr Vakhovskyi (Alex Waha)".

### Added

- Plugin layout: the pipeline tools, skills and hook registration now live under
  `plugin/` (`plugin/tools`, `plugin/skills` with 43 skills,
  `plugin/hooks/hooks.json` with all 46 hooks, `plugin/.claude-plugin/plugin.json`)
  (task-0038).
- GitHub marketplace manifest `.claude-plugin/marketplace.json`: marketplace
  `agentry` serving the plugin `agentry` from `./plugin`, installable with
  `claude plugin marketplace add AlexWaha/agentry` (task-0039).
- Install records for a local install and for the first install from GitHub,
  `docs/technical/plugin-install-2.1.299.md` (task-0039, task-0040).
- Record of the two green phase-7 entry cycles,
  `docs/technical/phase7-entry-cycles.md` (task-0037).
- Release notes for this version, `docs/release/v0.2.0.md`, and this changelog.
- Add `docs/extending.md`, a user guide page on adding agents, rules and skills and on
  running beside other plugins (task-0148).
- Read-only local dashboard: the command `/agentry:dashboard` starts a stdlib Python
  server on `127.0.0.1` (`plugin/tools/dashboard/server.py`) that opens the browser on a
  per-launch token URL, and `/agentry:dashboard stop` ends it. It shows the pipeline,
  tasks, activity, memory and harness over a versioned REST API and a WebSocket, and
  never writes to the project (task-0149, from task-0140 to task-0147).
- The dashboard front end (`dashboard/`: React, TypeScript, Vite) with its built output
  committed in `plugin/tools/dashboard/dist/`, so a user needs no Node; the server checks
  the build against a manifest before it binds a port (task-0142).
- The dashboard Graphs page draws the memory graph and the code graph as a live force
  simulation on a canvas, as Obsidian does: wheel and pinch zoom, background pan, drag a
  node (it is pinned while held and the layout reheats), hover highlighting of a node and
  its neighbours, labels on hover and when zoomed in, Fit to view and Reset view, and a
  details strip with a link for task nodes. A graph over the node cap keeps its most
  linked nodes together with the nodes that connect them instead of a disconnected set.
  The canvas is one tab stop with arrow-key node navigation, "Show as table" stays, and
  prefers-reduced-motion settles the layout without animation (task-0152).
- User page `docs/dashboard.md`: start, stop, security model, SSH tunnel note and
  troubleshooting, with the measured behaviour of the server on stop and on session end
  (task-0143).

### Changed

- The project root is resolved only from `CLAUDE_PROJECT_DIR`, and the plugin
  root from `CLAUDE_PLUGIN_ROOT` (exported as `AGENTRY_ROOT`); hook commands
  quote `${CLAUDE_PLUGIN_ROOT}` (task-0038).
- Agents carry a `profile:` key that the gates look up by `agent_type`; an
  unknown agent is treated as read-only (task-0038).
- Documentation rewritten as a landing README plus a user guide under `docs/`,
  with a local preview tool (`scripts/docs_preview.py`) (task-0138).
- The five gate commands in the template `pipeline.json` that run
  `artifact_gate.py` (plan, brief and spec) and `ui_evidence.py` (test) now use
  `{plugin}/tools/pipeline/` instead of `{root}/.claude/tools`. Verified: the plan
  gate of a feature task passes in a project that has only the plugin installed.
  The test-stage command is specific to this repository; a new project sets its
  own at onboarding (task-0137).
- The harness deploy runbook in `rules/orchestration.md` describes the GitHub
  release flow: version bump and changelog on a task branch, gated merge, tag,
  approved push of `main` and the tag, `claude plugin update`, restart (task-0137).
  It now starts with a dashboard build check: in a clean clone `npm ci && npm run
  build` must leave `git status --porcelain plugin/tools/dashboard/dist` empty before
  the version bump and tag (task-0143).
- The template `settings.json` no longer registers a status line, because
  `AGENTRY_ROOT` is not known to be set for a status line command; the script stays
  in the plugin for an opt-in by absolute path (task-0137).
- The README describes the harness as a plugin to install, pin, upgrade and roll
  back. The plugin ships the tools, skills and hooks; agents and rules come from
  `.claude/`; the pipeline config, project overlay and task templates come from
  `.agentry/`.

### Fixed

- The test suite no longer appends to the host session's `CLAUDE_ENV_FILE`
  (task-0039).
- The dashboard showed every finished run as "Done - In progress" with a pulsing ring: a
  run at the last stage with nothing awaited now has no current stage, and the Overview
  lists only runs in flight (task-0151).
- The dashboard stepper of an abandoned run read "Merged" and "Approved"; it now reads
  "Abandoned" and claims neither (task-0152).

[Unreleased]: https://github.com/AlexWaha/agentry/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/AlexWaha/agentry/releases/tag/v0.2.0
