# 2026-07-08 - Template upgrade: agents, skills, memory layers, diff-review, orchestrator gate

- Orchestrator hard gate: `orchestrator_gate` in pipeline.json + pretool_gate.py
  denies main-thread Edit/Write and shell file-writes outside .claude/, docs/,
  README*, root CLAUDE.md. Subagents detected via payload agent keys +
  transcript_path (/subagents/ or agent-*.jsonl) and exempted. Integration-tested
  with real headless runs (main denied, subagent allowed).
- All 31 agents rewritten to one compact template: 6546 -> 2255 lines. Frontmatter:
  description with "Use when" trigger, skills preload (1-3 core), gate profile
  hooks on every agent (8 previously hookless fixed), memory/mcpServers preserved.
  Duplicated blocks moved to rules (pipeline.md: context absorption;
  quality-standard.md: verification discipline + pre-flight; communication.md:
  {{COMM_LANG}}, hardcoded-Russian bug removed).
- All 23 skills: trigger-rich descriptions, allowed-tools, bodies <200 lines with
  references/ (progressive disclosure), BACKLOG.md dead refs fixed (folder-based
  task model), TDD mode in write-tests, PRD intake in new-task. Catalog:
  .claude/skills/README.md with the P/O binding matrix.
- Memory layers (.claude/memory/): L1 codebase.md (session-start drift check via
  tools/memory/codebase_sync.py against git heads), L2 lessons.md, L3 patterns.md
  + patterns/. SubagentStart hooks inject per agent type (planning: l1+l3; spec:
  l1+l2; dev/review: l2). Post-task distill gate: tools/memory/update.py stamps in
  .claude/state/memory/, stop_gate blocks the next task until stamped (--none legal).
- New pipeline stage diff-review (between review and ready): tools/review/
  diff_review.py - stdlib localhost SPA, side-by-side highlighted diff, inline
  line-anchored comments, Approve / Request changes; verdict JSON in
  .claude/state/review/<task>.json; server shuts down after the verdict.
  advance.py "interactive" branch consumes it: approved -> ready;
  changes_requested -> comments appended to task file EOF (## CEO Review
  Feedback) + reset to implement. Edit freeze extended to diff-review stage.
- Documentation loop: handoff.latest_main_task_undocumented() + stop_gate block -
  single-task backlog requires the latest task-tagged main commit documented
  before implementation (flag handoff.document_latest_on_start).
- Em/en dashes purged repo-wide (277 in legacy rules/project files + CLAUDE.md).
- Lesson: settings-level PreToolUse hooks DO fire inside subagents - any
  main-thread-only gate must detect subagents via hook payload, and the
  transcript_path signal is the reliable one across harness versions.
