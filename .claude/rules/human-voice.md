# Human Voice - Anti-AI-Signature Rule (deliverables)

Written deliverables that read like AI slop cost the team credibility: investors skim a whitepaper and smell a template, a customer reads docs that say nothing in many words, a reviewer loses trust in a business plan padded with hype. This rule defines the writing standard for prose deliverables and the pipeline that enforces it.

It is derived from the blader/humanizer methodology (33 patterns across five categories, a two-pass rewrite, voice calibration), retuned for technical and business prose.

## Scope

**Applies to: prose in written deliverables.** Documents in `docs/` (business, market, finance, product, technical, marketing, risk, growth, guides), whitepapers, pitch-deck content and speaker notes, READMEs, CHANGELOG entries, ADR prose, commit messages, PR descriptions, status reports, marketing copy.

**Does NOT apply to:**
- **Code** - logic, identifiers, and code comments are governed by `coding-style.md`, not this rule. (Comment prose can borrow the "say something specific" spirit, but no humanization pass.)
- **Chat with the CEO/Orchestrator** - governed by `communication.md` (friendly, energetic, emoji welcome). This rule is about artifacts that ship, not the working conversation.
- **Structured data** - tables, YAML/JSON, OpenAPI specs, Mermaid diagrams, code blocks, metric rows. Never "humanize" data.

The split matters: `communication.md` keeps chat warm and emoji-friendly; this rule keeps shipped documents clean and forensic. They do not conflict because they cover different surfaces.

## The 33 Patterns

What the AI does, and what to do instead. Examples are dev/business-flavored.

### Category 1 - Content
1. **Significance inflation.** "a pivotal moment in the evolution of SaaS" -> state the specific fact and date. Not "transformative milestone" but "cut p95 latency from 800ms to 120ms in the March release".
2. **Notability name-dropping.** "trusted by industry leaders" -> name the customers and the year, or cut it.
3. **Superficial `-ing` analysis.** "...shipping the feature, underscoring our commitment to quality" / "reflecting market demand". Cut the trailing editorial or replace with a metric.
4. **Promotional language.** "best-in-class", "world-class", "cutting-edge", "revolutionary", "game-changing" -> describe what it does; let the reader judge.
5. **Vague attributions.** "studies show", "experts agree", "it is widely known" -> cite the source + year, or cut.
6. **Formulaic challenge framing.** "despite challenges, the team delivered" -> name the actual blocker and the actual fix.

### Category 2 - Language
7. **AI vocabulary.** Drop: "leverage", "utilize", "delve", "robust" (unless benchmarked), "seamless", "holistic", "synergy", "testament", "landscape", "realm", "pivotal", "underscore", "spearhead", "facilitate", "streamline" (unless quantified). Use plain words.
8. **Copula avoidance.** "serves as", "boasts", "stands as", "functions as" -> "is" / "has". ("The API has 12 endpoints," not "boasts a suite of 12 endpoints.")
9. **Negative parallelism.** "it's not just a tool, it's a platform" / "not only fast but also scalable" -> state the points directly.
10. **Rule of three.** Forced triplets of adjectives, nouns, or clauses ("fast, scalable, and maintainable" when only two matter). Use the real count.
11. **Synonym cycling.** "the platform... the solution... the offering... the system" for one product -> pick the defined term and repeat it. (Terminology consistency is a documentation quality norm; see `documentation.md`.)
12. **False ranges.** "everything from startups to enterprises" -> name the actual segments.
13. **Passive voice / subjectless fragments.** "it was decided", "performance was improved", "No setup required". Active voice and a named actor (per `documentation.md` "active voice preferred"): "the team decided", "the cache cut load time by 40%". (Passive is fine only where the actor is genuinely irrelevant.)

### Category 3 - Style and Format
14. **Em dash / en dash.** NEVER `-` (U+2014) or `-` (U+2013). Always hyphen-minus `-`. (Reinforces `communication.md`.)
15. **Boldface overuse.** Bolding random terms/acronyms for emphasis. Reserve bold for genuine labels and table headers.
16. **Fake lists.** A flowing argument chopped into bullets that add no structure -> write it as prose. NOTE: genuine structured lists, comparison tables, and step sequences are CORRECT here (`documentation.md` mandates tables for structured data) - this targets prose-disguised-as-bullets, not legitimate lists.
17. **Title Case Headings.** Use sentence case ("Revenue model", not "Revenue Model") except proper nouns / fixed terms.
18. **Emojis.** None in deliverables. (Chat is exempt per `communication.md`.)
19. **Curly quotes.** Straight quotes only, consistently.
20. **Hyphenation inconsistency.** "data-driven" / "end-to-end" / "decision-making" - pick one form per compound and keep it.
21. **Persuasive-authority tropes.** "At its core,", "Make no mistake,", "The reality is," -> cut; make the claim.
22. **Signposting.** "Let's dive in", "Here's what you need to know", "In this section we will" -> say the thing.
23. **Fragmented headers.** A heading must carry meaning without the sentence beneath completing it.

### Category 4 - Communication artifacts
24. **Chatbot artifacts.** "I hope this helps!", "Let me know if you need anything else." -> delete from any deliverable.
25. **Knowledge-cutoff / source disclaimers.** "as of my last update", "while details are limited" -> delete; mark a real gap as a documented assumption or TODO instead (per `documentation.md`).
26. **Sycophancy.** "Great question!", "You're absolutely right" -> delete.

### Category 5 - Filler, hedging, rhetoric
27. **Filler phrases.** "in order to"->"to", "due to the fact that"->"because", "at this point in time"->"now", "a number of"-> a count.
28. **Excessive hedging.** "could potentially possibly help" -> "may help". Business docs should be confident-but-sourced; one hedge max, none stacked.
29. **Generic conclusions.** "The future looks bright", "this positions us for success" -> a specific plan, a dated milestone, or nothing.
30. **Diff-anchored writing.** In commits/CHANGELOG, describe what the change IS and DOES, not vaguely "updated/improved". ("Add rate limiting to /login (5 req/min)", not "improved security".)
31. **Manufactured punchlines / staccato drama.** "And it just works." -> vary sentence length naturally; do not engineer drama.
32. **Aphorism formulas.** "Simplicity is the ultimate sophistication." -> make the actual claim or cut.
33. **Rhetorical openers.** "Honestly?", "Here's the thing -" -> open with substance.

## Required Style (positive)

- Specific facts where AI uses adjectives: real numbers, real dates, real names, real metrics.
- Vary sentence length; a one-sentence paragraph for emphasis is allowed.
- Open paragraphs with substance, not a transition word.
- Concrete verbs: "shipped", "built", "cut", "raised", "measured", "deployed", "benchmarked".
- Banned emotive filler in deliverables: "passionate", "exciting", "amazing", "incredible", "good" (unqualified).
- Explain each acronym once.

### The "So What?" rule
Every fact in a deliverable must carry its own significance. After a factual sentence, answer the implicit "So what?" with a sourced consequence, not an adjective.
- Draft: "We added caching." (So what?)
- Best: "We added Redis caching on the product feed, cutting p95 from 800ms to 120ms (see benchmark)."

If a sentence states a fact but not why it matters, chain the consequence or cut it.

## Voice Calibration

When a deliverable has a target voice (a founder's pitch, a CEO-signed vision doc, a customer-facing post in an established brand voice), calibrate to a real sample.
1. Ask for 2-3 paragraphs the person/brand actually wrote.
2. Read for sentence rhythm, word choices, formality, how they open/close.
3. Rewrite to match, without inventing facts.
4. If no sample, default to plain, specific, varied prose.

Brand-voice deliverables also defer to `brand-guardian` where a brand guide exists.

## Two-Pass Rewrite

`humanizer` rewrites twice: Pass 1 fixes all 33 patterns + calibration; Pass 2 is a cold re-read ("what still reads as AI?") because Pass 1 fixes introduce new tells. Only then does `ai-detector` scan.

## Workflow

1. The producing agent (usually `technical-writer`, or any agent writing a deliverable) drafts the document.
2. `humanizer` runs the two-pass rewrite (with calibration where a target voice exists).
3. `ai-detector` scans for the 33 patterns and reports.
4. The producing agent fixes flagged items.
5. `reviewer` includes an AI-voice lens in its document review.

Mandatory for investor-facing and customer-facing deliverables (whitepapers, pitch decks, business plans, marketing copy, public READMEs). Recommended for internal docs; required if they read as AI-generated.

## Why

Investor-, customer-, and public-facing prose is judged partly on how it reads. Templated AI phrasing signals low effort and erodes trust in the underlying claims, exactly when the document is trying to build it. Clean, specific, forensic prose does the opposite.
