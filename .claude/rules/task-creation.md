# Task Creation

Rules governing how new tasks are decomposed and written. Applies to all task files, wherever they currently sit - `.claude/tasks/backlog/` (queued), `active/` (in flight), or `done/` (merged). New tasks are created directly in `.claude/tasks/backlog/`; there is no `status:` field, the folder is the state.

---

## Cross-Layer Impact Analysis (MANDATORY)

When creating a backend task, you **must** simultaneously evaluate frontend impact and decide whether a paired frontend task is needed. Backend changes that surface new entities, new fields, new endpoints, new payload shapes, new statuses, or new business rules almost always require corresponding frontend work for users to see/manage/act on the change.

The same rule applies in reverse: frontend tasks that depend on data the backend does not yet expose require a backend task first.

### When you create a backend task

For every backend task, fill in a **Frontend Impact** section that answers all four questions:

1. **What does this expose to users?** New entity, new field, new endpoint, new validation, new permission - anything user-facing
2. **Does an existing frontend task already cover it?** Cite task ID. If yes, link via `depends_on` or note in scope
3. **Does a new frontend task need to be created?** If yes, create it in the same session. Include the new task ID in this backend task's notes
4. **What stays out of scope on the frontend?** Explicit non-goals (e.g. "no admin UI v1, exposed only via API")

If the backend task creates an entity with no UI surface (internal job, background worker, infrastructure plumbing), document that explicitly: `Frontend Impact: none - internal only`. The check still happens; the answer can be "none."

### When you create a frontend task

Mirror the same check for backend dependencies:

1. **What backend endpoints / payload shape does this need?**
2. **Do they exist?** If not, create the backend task first or in the same session
3. **What backend work is out of scope?** (e.g. "uses existing endpoint, no schema change")

Add `depends_on:` in the frontmatter pointing to the backend task ID.

---

## Why

We have seen the gap several times: backend ships a feature, frontend has no surface to drive it, admin/ops cannot use it, work sits unused for weeks. By forcing the cross-layer check at task-creation time, we surface gaps when they are cheap to fix (planning) instead of after merge (rework + coordination overhead).

The check is also a forcing function for thinking about the user. If you cannot answer "what does this expose to users", the backend scope is probably wrong - either too narrow (missing the user surface) or too broad (mixing infra with feature work).

---

## Task File Section

Both backend and frontend task files must include a `## Frontend Impact` (backend) or `## Backend Dependencies` (frontend) section. The template enforces this. Skipping the section is grounds for the task to be rejected at review.

Example for a backend task:

```markdown
## Frontend Impact

- **Exposes:** new `POST /api/v1/admin/<resource>/{id}/<action>` endpoint
- **Existing frontend task:** task-XXXX (Admin UI) - extend its scope to include the new action
- **New frontend task needed:** no - task-XXXX covers it
- **Out of scope on frontend:** bulk variant of the action (single-item only in v1)
```

Example for an internal-only backend task:

```markdown
## Frontend Impact

- **Exposes:** none - background worker / internal pipeline
- **Existing frontend task:** n/a
- **New frontend task needed:** no
- **Out of scope on frontend:** all - this is internal plumbing
```

---

## Pairing Convention

When a backend task and its paired frontend task are created together, link them bidirectionally in `depends_on` and reference each other in the Notes section. This makes the relationship discoverable from either side.

```yaml
# backend task
depends_on: [task-XXXX, task-YYYY]
# Notes: Frontend task: task-ZZZZ
```

```yaml
# frontend task
depends_on: [task-WWWW]
# (depends_on the backend task it consumes)
```

---

## Epic and Spec Linkage

When a task is born from an epic (skills/new-epic):

- Frontmatter carries `epic: epic-XXXX` and `spec: spec-XXXX`; the epic's
  `tasks: []` list carries the task id back - bidirectional, discoverable from
  either side
- Acceptance criteria cite spec FR ids (`- [ ] (FR-2) ...`) - the Spec Trace
- Tasks are created as files in `.claude/tasks/backlog/`; only the CEO's
  approval of the epic breakdown - and then `advance.py` starting the task -
  moves them to `active/`. The Stop hook only reads `backlog/`, and only takes
  from it when the approvals level grants the `take` checkpoint, so nothing
  starts prematurely
- `advance.py` refuses to register a task whose `spec:` is not `approved` -
  the linkage is enforced, not decorative

## Anti-Patterns

- Backend task with no `Frontend Impact` section - **reject**
- Backend task that says "frontend will figure it out later" - **reject** (decide now or document why deferred)
- Frontend task with no `Backend Dependencies` section - **reject**
- Creating a backend task that adds a new entity without checking whether admin/ops need to manage it - **always need to check**
- Bundling backend + frontend in one task file - **split into two paired tasks**
