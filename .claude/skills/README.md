# Skills Catalog

24 skills, each a directory with `SKILL.md` (procedure, <200 lines) plus
optional `references/` files (templates and code examples, loaded on demand).
Agents preload their core procedures via the `skills:` frontmatter field
(P below); everything else is invoked at runtime through the Skill tool (O).
Descriptions carry "Use when" triggers so model invocation fires reliably.

| Skill | What it does | P (preloaded by) | O (on demand by) |
|---|---|---|---|
| api-design | REST endpoints, schemas, error formats, OpenAPI spec | architect | senior-backend-dev, spec-developer |
| bug-fix | RED-GREEN debugging: failing test -> root cause -> minimal fix | senior-backend-dev, senior-frontend-dev, qa-engineer | incident-response-commander |
| business-plan | Full business plan from prior research docs | business-analyst | financial-analyst |
| db-design | Schema, relationships, indexes, ER diagram, migration plan | architect, data-engineer | senior-backend-dev, spec-developer |
| deep-review | Multi-lens severity-ranked review (code/arch/docs/config) | reviewer | architect, security-engineer, accessibility-auditor |
| feature-scaffold | Complete API feature: migration -> tests, all layers | senior-backend-dev, senior-frontend-dev, rapid-prototyper | data-engineer, ux-ui-designer |
| financial-model | Projections, P&L, breakeven, unit economics, 3 scenarios | financial-analyst | business-analyst |
| frontend-standards | Layered tokens, feature-based architecture, reusable components, type-safe i18n, no hardcoding | senior-frontend-dev | reviewer, ux-ui-designer, rapid-prototyper, accessibility-auditor |
| gtm-strategy | Go-to-market: segments, channels, launch, KPIs | marketing-strategist | brand-guardian |
| health-check | Full quality gate: format, lint, tests, pattern scan | performance-benchmarker, incident-response-commander | qa-engineer, devops-engineer, security-engineer, seo-specialist, accessibility-auditor |
| ideation | Product vision, audience analysis, personas from a raw idea | - | product-manager, rapid-prototyper, ux-researcher, ux-ui-designer, marketing-strategist, feedback-synthesizer |
| infrastructure | Docker dev stack + CI/CD, verified end-to-end | devops-engineer | - |
| market-research | TAM/SAM/SOM, competitor matrix, SWOT, positioning | business-analyst, marketing-strategist, ux-researcher | product-manager, seo/geo-specialist, evidence-collector, feedback-synthesizer |
| migration | Safe, reversible DB migrations + cascade updates | data-engineer | architect, senior-backend-dev, devops-engineer |
| new-epic | Epic + linked task decomposition from an approved spec | product-manager | - |
| new-task | Task file with PRD-shaped intake and complete frontmatter | product-manager | orchestrator, spec-developer, sprint-prioritizer |
| pitch-deck | 12-slide investor deck content + PPTX build script | - | business-analyst, financial-analyst, marketing-strategist, brand-guardian |
| risk-assessment | Scored risk matrix with mitigation and contingency | security-engineer | architect, business-analyst, sprint-prioritizer, incident-response-commander |
| self-learning | Record/distill/recall/curate lessons and patterns | - (all agents, nudged by hooks) | everyone |
| stakeholder-html-deck | Self-contained HTML slide deck with presenter view | - | financial-analyst, marketing-strategist |
| status-report | Project status from actual task files and pipeline state | sprint-prioritizer | orchestrator, product-manager, content-manager, technical-writer |
| update-docs | Post-phase documentation audit and changelog | technical-writer | content-manager |
| whitepaper | Technical + business whitepaper for investors/partners | - | content-writer, technical-writer |
| write-tests | 5-category endpoint coverage + TDD mode | qa-engineer | senior-backend-dev, senior-frontend-dev |

Conventions for authoring a new skill: frontmatter first line `---`;
`description` = WHAT + "Use when <triggers>" (combined under 1000 chars);
`allowed-tools` limited to what the procedure needs; body <200 lines with
numbered Steps and an Output checklist; overflow into `references/<topic>.md`
linked from a final "References (read only when needed)" section. Never set
`disable-model-invocation` on a skill an agent preloads.
