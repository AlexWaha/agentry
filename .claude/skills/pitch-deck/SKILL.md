---
name: pitch-deck
description: Generate complete investor pitch deck content (12 slides with speaker notes) plus a runnable Python script that builds the PPTX file. Use when the CEO asks for a pitch deck, investor deck, fundraising presentation, or "deck for the raise", and only after product-vision, business-plan, market-research, and financial-model documents already exist.
allowed-tools: Read, Grep, Glob, Write
---

# Pitch Deck - Investor Presentation Content + PPTX Generation

> STATUS: DEFERRED
> Not active in current project phase (backend API only).
> Will be activated when business planning/analysis work begins.
> Do not invoke until explicitly activated by CEO.

Produces complete investor pitch deck content (structured slide content with
speaker notes) and a Python script that generates a professional PPTX file,
following the standard 10-12 slide investor deck format.

## Steps

1. **Confirm prerequisites exist.** All prior phases must be done - the deck
   synthesizes everything: `docs/business/product-vision.md`,
   `target-audience.md`, `user-personas.md`, `business-plan.md`,
   `revenue-model.md`, `unit-economics.md`; `docs/market/market-research.md`,
   `competitive-landscape.md`, `positioning.md`; `docs/finance/financial-model.md`,
   `projections.yaml`, `breakeven-analysis.md`; `docs/product/whitepaper.md`
   (if it exists). Confirm Python 3.x is available for testing the PPTX script.
2. **Read every prior deliverable.** Extract the most compelling data points:
   market size (TAM/SAM/SOM), growth rate (CAGR), key competitor weaknesses,
   unit economics (CAC, LTV, LTV:CAC), revenue projections (Year 1, Year 3),
   breakeven timeline, funding ask. Also read `basic-idea.txt` for the
   original concept.
3. **Write content for each of the 12 slides.** Title, subtitle (optional),
   3-5 bullets max, a key visual description, and 3-5 sentences of speaker
   notes per slide. Full slide-by-slide structure, content, and speaker-note
   scripts: `references/deck-template.md`.
4. **Create the slide content files.** One markdown file per slide under
   `docs/presentations/pitch-deck/`, plus a README explaining usage. Exact
   directory layout and file format: `references/deck-template.md`.
5. **Write the PPTX generator script.** A complete, runnable
   `docs/presentations/scripts/generate_pitch.py` using `python-pptx` - no
   skeleton functions with `...` placeholders. Branding constants, slide
   generation functions, and full script skeleton to follow:
   `references/deck-template.md`.
6. **Create supporting directories.** `docs/presentations/pitch-deck/`,
   `scripts/`, `output/` (gitignored), `assets/` with a README listing
   expected asset files (logo, screenshots, team photos).
7. **Present the summary to the CEO.** Deck structure, key narrative flow
   (Problem -> Solution -> Market -> Business Model -> Traction -> Ask),
   strongest and weakest slides, PPTX generator status, what's still needed
   from the CEO (team bios, screenshots, branding), and request explicit
   approval. Wait for CEO/CTO approval before considering the work done.

## Output checklist

- [ ] All 12 slides have content files with title, bullets, visual
      description, and speaker notes
- [ ] Each slide has 5 or fewer bullet points
- [ ] Every number on every slide traces to an existing doc
- [ ] Narrative flows logically slide to slide
- [ ] Speaker notes tell a story, not just repeat the bullets
- [ ] PPTX script is complete, runnable, produces a valid .pptx file
- [ ] Script has a branding configuration section and handles missing
      python-pptx gracefully
- [ ] Slide dimensions are 16:9 widescreen; font sizes are readable
- [ ] The Ask slide has a specific dollar amount and use-of-funds breakdown
- [ ] Competition slide uses a positioning matrix, not a feature checklist
- [ ] README.md explains how to use the deck and generator
- [ ] Summary presented to CEO; approval requested

## References (read only when needed)

- [references/deck-template.md](references/deck-template.md) - full
  slide-by-slide content template (all 12 slides with content and speaker
  notes), slide content file format, PPTX generator script structure and
  branding configuration, supporting directory layout, output files table
