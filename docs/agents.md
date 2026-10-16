# Agents

Agentry ships 31 agents. You talk to one session, the orchestrator, which formalizes your request, dispatches the right agent for each stage, verifies gate results and reports back. The orchestrator does not write code. A hook denies its file writes outside `.claude/`, `.agentry/`, `docs/`, any `README*` file and the root `CLAUDE.md`, so changing code always means dispatching the agent that owns it.

## Table of contents

- [Departments](#departments)
- [Gate profiles](#gate-profiles)
- [Technical department](#technical-department)
- [Support department](#support-department)
- [Business department](#business-department)
- [Content and growth department](#content-and-growth-department)
- [Which stage runs which agent](#which-stage-runs-which-agent)

## Departments

<p align="center"><a href="assets/agents.png"><img src="assets/agents.png" alt="You talk to the Orchestrator, which dispatches four departments. Technical (7): architect, senior-backend-dev, senior-frontend-dev, data-engineer, devops-engineer, qa-engineer, rapid-prototyper. Support (13): reviewer, security-engineer, performance-benchmarker, accessibility-auditor, evidence-collector, incident-response-commander, technical-writer, ux-ui-designer, ux-researcher, spec-developer, product-manager, sprint-prioritizer, feedback-synthesizer. Business (4, dormant): business-analyst, financial-analyst, marketing-strategist, brand-guardian. Content and growth (7): content-manager, content-writer, humanizer, ai-detector, editor, seo-specialist, geo-specialist." width="100%"></a></p>

The Technical and Support departments are active from the start. Business stays dormant until you turn it on. Content and growth agents are used when content or SEO work begins.

## Gate profiles

Every agent declares a profile in its definition. The plugin's hooks enforce it inside the agent itself, not through the prompt.

| Profile | May do | May not do |
|---|---|---|
| `dev` | Edit code and run commands | Approve its own checkpoints, run commands listed in `gates.dev_forbidden_commands` (per stack, usually the full test suite), or commit without a recorded approval |
| `docs` | Write under `.claude/`, `.agentry/`, `docs/` and any `README*` file | Touch application code |
| `readonly` | Read, search and run commands that do not change anything | Edit or write files, including through shell redirects |

An agent the hooks cannot identify is treated as read-only. Agents run without permission prompts, because the profile gates, not a dialog, are what restrain them.

## Technical department

| Agent | Profile | Dispatched when |
|---|---|---|
| `architect` | readonly | Planning a feature or system, DB schema, API contract decisions |
| `senior-backend-dev` | dev | Backend feature, bugfix, refactor, migration |
| `senior-frontend-dev` | dev | Web or mobile UI feature, component work |
| `data-engineer` | dev | Data pipelines, ETL, warehouse schemas, data quality |
| `devops-engineer` | dev | Containers, CI/CD, deploys, infrastructure |
| `qa-engineer` | dev | The `test` stage: writing and running tests, quality gates |
| `rapid-prototyper` | dev | A throwaway MVP or spike to test an idea fast |

## Support department

| Agent | Profile | Dispatched when |
|---|---|---|
| `reviewer` | readonly | The `review` stage, after every implementation |
| `security-engineer` | readonly | Security audit, threat model, vulnerability review |
| `performance-benchmarker` | readonly | Load tests, performance baselines and regressions |
| `accessibility-auditor` | readonly | Accessibility audit of UI work |
| `evidence-collector` | readonly | Screenshots or logs as proof of UI behavior during review |
| `incident-response-commander` | readonly | Production incident triage and post-mortems |
| `technical-writer` | docs | Documentation, API docs, changelog after a phase |
| `ux-ui-designer` | docs | Wireframes, user flows, design review |
| `ux-researcher` | docs | Interviews, surveys, usability findings |
| `spec-developer` | docs | Writing the spec from the architect's plan |
| `product-manager` | docs | Splitting an approved spec into an epic and tasks |
| `sprint-prioritizer` | docs | Ordering the backlog, status reports |
| `feedback-synthesizer` | docs | Consolidating user feedback into themes |

## Business department

Dormant by default. Turn it on when business planning begins.

| Agent | Profile | Dispatched when |
|---|---|---|
| `business-analyst` | docs | Market research, business plan, requirements analysis |
| `financial-analyst` | docs | Financial models, unit economics, pricing, runway |
| `marketing-strategist` | docs | Go-to-market strategy, channels, launch planning |
| `brand-guardian` | docs | Brand consistency review of outward-facing assets |

## Content and growth department

| Agent | Profile | Dispatched when |
|---|---|---|
| `content-manager` | docs | Content strategy, editorial calendar, briefs |
| `content-writer` | docs | Drafting copy from a brief, before the humanizer |
| `humanizer` | docs | Rewriting prose to remove machine-written patterns, after the content writer |
| `ai-detector` | readonly | A verdict on machine-written patterns, after the humanizer and before review |
| `editor` | docs | Line and copy edit, factual integrity, terminology |
| `seo-specialist` | docs | Technical and on-page SEO, keyword strategy |
| `geo-specialist` | docs | Optimizing for citations in AI-generated answers |

## Which stage runs which agent

| Stage or job | Agents |
|---|---|
| Plan pipeline | `architect` (formalize, draft), `reviewer` (plan-review), `spec-developer` (spec), `product-manager` (breakdown), `sprint-prioritizer` (ordering) |
| `plan`, `implement` | `senior-backend-dev`, `senior-frontend-dev` |
| `test` | `qa-engineer` |
| `review` | `reviewer`, `security-engineer`, plus `evidence-collector` for UI work and `performance-benchmarker` for performance-sensitive work |
| Infrastructure | `devops-engineer` |
| Documentation | `technical-writer` |
