# `project/` - Project-Specific Overlay

This directory holds everything that belongs to the **specific project this template is currently used in**, not to the universal template itself.

## Why this exists

The `.claude/` agent system is designed as a reusable template that works on any project/stack/language. Project-specific content (current domain, current stack conventions, current architecture) lives here so the rest of `.claude/` stays clean and portable.

## Contents

| File | What it is |
|------|-----------|
| `project-context.md` | Current product description, domain terminology, key features |
| `architecture.md` | Current project architecture (module layout, dependency rules) |
| `api-conventions.md` | Current project API conventions (framework-specific examples) |
| `stack.md` | Current tech stack details: commands, tools, framework quirks |

## When you copy this template to a new project

1. **Delete** everything in `project/` except `README.md`
2. Fill in `project/project-context.md` with your new product's description, key concepts, domain terminology
3. Draft `project/architecture.md` once module/layer decisions are made
4. Draft `project/api-conventions.md` once framework conventions are settled
5. Draft `project/stack.md` with concrete commands: test, format, lint, build, dev
6. Update `.claude/CLAUDE.md` placeholders (see `.claude/_onboarding.md`)

## Rule file stubs

Root `rules/architecture.md` and `rules/api-conventions.md` are thin stubs that point here. Keep them as stubs - the meat lives in this folder.

## Why not just edit the root `rules/`?

Because the goal is: anyone should be able to `cp -r .claude/ new-project/.claude/`, clear `project/`, and start fresh without having to track down every Laravel/Python/Django reference buried inside generic rule files.

`project/` is the single place project specificity is allowed.
