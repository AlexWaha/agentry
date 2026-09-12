---
name: stakeholder-html-deck
description: Build a self-contained single-file HTML slide deck with speaker notes, brand colors, and a synced presenter view for a stakeholder or leadership presentation. Use when asked to "make a presentation / deck / slides" for leadership, clients, a demo day, or an all-hands, when the audience is non-technical or mixed, or when results/data need to be argued as proof in front of stakeholders. Not for internal engineering docs or README content aimed at technical readers.
allowed-tools: Read, Grep, Glob, Write, Bash
---

# Stakeholder HTML Deck

A self-contained HTML slide deck (one file, no build step) is the best format for a high-impact stakeholder presentation. It is offline-safe, looks premium, and supports a synced presenter view that no slide tool gives you for free. Core principle: **the audience needs the story and the proof, not the implementation.** Lead with the business question, show data that answers it, and make every claim explainable in one breath.

## Steps

1. **Gather the substance first.** Read the data/results. If the system being shown has logic (scoring, thresholds, formulas), dispatch an Explore agent to get exact numbers and rules from the codebase. Verify the data against the code so on-slide claims are correct. Derive the story from the data, do not invent it.
2. **Confirm two things with the user before building:** slide language, and visual style (light-corporate vs dark-premium vs brand-accented). Cheap to ask, expensive to redo.
3. **Pull real brand colors** (see below). Do not guess.
4. **Build from the template** (`presenter-deck-template.html` in this skill dir). It already has the chrome, keyboard nav, counter animation, and the presenter view wired up.
5. **Write the speaker script** as a separate `*-script.md` AND embed the same notes in the deck's `NOTES[]` array so the presenter view stays in sync with the document.
6. **Verify in a real browser** (see Verification). Screenshot the key slides. Test the presenter view actually opens and syncs.
7. Clean up screenshots/`.playwright-mcp`/`nul` artifacts and stop any local server.

## Narrative arc (default 12-14 slides)

1. Title (one-line value proposition, no jargon)
2. The problem (why anyone should care, 3 pain points)
3. What we built (capabilities as benefits, not features)
4. How it works (3-step pipeline, plain words)
5. The model (thresholds + the key formula, visual)
6. Any modes/variants the engine has
7. The demo setup (what you tested and why)
8-11. Results tables, each followed by an insight callout that says what the numbers prove
12. The money slide (the single most convincing result, big and clean)
13. Impact (clear / flexible / working-now, with stat counters)
14. What is next + thank you

The insight-after-table rhythm is what makes a deck persuasive: never show a table without telling the audience what it means.

## Brand colors (extract, do not guess)

Drive a browser to the company site and read the real computed styles:

```js
// run via browser_evaluate on the live homepage
() => {
  const tally = sel => { const m={}; document.querySelectorAll(sel).forEach(el=>{
    const c=getComputedStyle(el); [c.backgroundColor,c.color].forEach(v=>{ if(v&&v!=='rgba(0, 0, 0, 0)')m[v]=(m[v]||0)+1; }); });
    return Object.entries(m).sort((a,b)=>b[1]-a[1]).slice(0,8); };
  return { actions: tally('a,button,[class*=button]'), headings: tally('h1,h2,h3'),
           svg:[...new Set([...document.querySelectorAll('svg [fill]')].map(e=>e.getAttribute('fill')))].slice(0,8) };
}
```

The dominant button background is usually the primary brand color; SVG logo fills give the deep brand color. Map brand colors onto meaning (e.g. brand green -> HIGH/MATCH badges) so the palette feels native, not decorative.

## Copy: humanize, kill the AI tells

Stakeholder copy must not read as machine-generated. Apply every time:

- **No em dashes or en dashes.** Restructure the sentence. Do not lean on " - " as a connector either; it is its own tell when overused.
- **No semicolons** in prose.
- **No buzzwords:** leverage, robust, seamless, at scale, explainable, defensible, holistic, empower, unlock, best-in-class.
- **Vary sentence length.** Allow short punchy fragments next to a longer line.
- **Concrete over abstract:** "she has none of the pure drafting skills" beats "lacks domain coverage".
- **No code, commands, class names, stack names, ticket IDs** on stakeholder slides. They mean nothing to the audience and date the deck.

## Formulas for non-technical viewers

Never show code. Render the formula as a typeset visual: a real fraction (numerator over a ruled denominator) in plain words.

```
Match Score  =  what the person can do  /  what the role needs  x 100
100 or more  ->  Good fit
below 100    ->  Not yet
```

Pair it with the threshold table as small labeled cards. Plain words, one screen, no notation the audience has to decode.

## Icons: generate SVG, never emoji

Emoji look amateur and render differently per OS. Use small inline stroke-style SVGs (viewBox 0 0 48 48, stroke = brand navy, width ~2.6, round caps). Make each icon mean something: a Venn for "roles overlap", increasing bars for "weighted scoring", an eye for "explainable". The template has a starter set.

## Control layout (avoid overlap)

Put all chrome in one bottom bar with three flex zones so nothing collides:

- left: brand label
- center: progress dots
- right: cluster of [presenter button] [prev] [counter] [next]

Give slides `padding-bottom` larger than the bar height (~96px). Top progress bar is separate and fixed.

## Presenter view (the hard-won part)

Goal: audience sees only slides; presenter sees synced notes + timer + next-slide, on a second window.

**Critical gotcha:** a `<script>` written into a popup via `document.write` **does not execute** in some embedded/automation browser contexts. Do **not** rely on the popup running its own JS or on `postMessage`/`BroadcastChannel`.

**Robust pattern:** drive the popup entirely from the opener.

1. Opener `window.open('about:blank', ...)`, then `document.write` a **static** HTML doc (styles + empty elements, no script).
2. Opener keeps the window handle and on every slide change writes directly into the child DOM: `presWin.document.getElementById('title').textContent = ...`.
3. Opener attaches the popup's button handlers and keydown listener as its own closures: `presWin.document.getElementById('n').onclick = () => next()`, `presWin.addEventListener('keydown', ...)`. They run in the opener's context, so they mutate the main deck and re-render the popup.
4. Opener runs the timer with `setInterval`, writing into the popup's clock element, and clears it when `presWin.closed`.

This makes navigation bidirectional (arrows from either window, buttons in the popup) with zero cross-window messaging. The template implements this exactly.

Keep speaker notes in a `NOTES[]` array indexed by slide, and a separate `*-script.md` with the same content plus timing and likely-questions. Keep them in sync.

## Verification

`file://` is blocked in the automation browser, so serve locally and drive it:

```bash
python -m http.server 8765   # run in background, from the deck's dir
```

Then with the browser tool: navigate to `http://localhost:8765/deck.html`, screenshot the title + formula + a results+insight slide + the money slide and read them back. Press `s`, select the popup tab, screenshot it, then confirm sync by clicking the popup's Next and checking both the popup `pos` and the opener `counter` advanced together. Clean up afterward (`rm -rf .playwright-mcp`, delete PNGs, `rm -f nul NUL`, stop the server).

## Common mistakes

| Mistake | Fix |
|---|---|
| Table with no takeaway | Add an insight callout after every results table |
| Guessed brand colors | Read computed styles off the live site |
| Emoji icons | Inline meaningful SVGs |
| Formula shown as code | Typeset fraction in plain words |
| Em dashes / semicolons / buzzwords in copy | Editor pass before shipping |
| Popup script silently dead | Drive popup from opener, no script inside it |
| Controls overlapping at bottom | Single three-zone bar + slide padding-bottom |
| Claimed it works without opening it | Serve + screenshot + test presenter sync |
| Ticket IDs / stack names on slides | Remove, audience does not care |

## Output checklist

- [ ] Data/results verified against the code before any claim goes on a slide
- [ ] Slide language and visual style confirmed with the user before building
- [ ] Brand colors extracted from the live site's computed styles, not guessed
- [ ] Built from `presenter-deck-template.html`; chrome, keyboard nav, counter animation, presenter view intact
- [ ] Speaker script written as `*-script.md` and mirrored in the deck's `NOTES[]` array
- [ ] Every results table is followed by an insight callout
- [ ] Copy has no em dashes, en dashes, semicolons, or buzzwords
- [ ] Formulas are typeset as plain-word fractions, never shown as code
- [ ] Icons are inline SVGs, never emoji
- [ ] Verified in a real browser: key slides screenshotted, presenter view opens and stays synced
- [ ] Cleanup done: screenshots, `.playwright-mcp`, `nul` artifacts removed, local server stopped

## References (read only when needed)

- [presenter-deck-template.html](presenter-deck-template.html) - the deck
  template with chrome, keyboard nav, counter animation, and presenter view
  already wired up; build every deck from this file
