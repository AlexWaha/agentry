# Accessibility Checklist and Templates

Reference material for the `accessibility-auditor` agent and the deep-review
accessibility lens. Moved out of the agent body to keep the role file compact.

## Accessibility Audit Report Template

```markdown
# Accessibility Audit Report

## Audit Overview
**Product/Feature**: [name and scope]
**Standard**: WCAG 2.2 Level AA
**Tools Used**: [axe-core, Lighthouse, screen reader(s), keyboard testing]

## Testing Methodology
**Automated Scanning**: [tools and pages scanned]
**Screen Reader Testing**: [VoiceOver/NVDA/JAWS - OS and browser versions]
**Keyboard Testing**: [all interactive flows tested keyboard-only]
**Visual Testing**: [zoom 200%/400%, high contrast, reduced motion]
**Cognitive Review**: [reading level, error recovery, consistency]

## Summary
**Total Issues Found**: [count]
- Critical: blocks access entirely for some users
- Serious: major barriers requiring workarounds
- Moderate: causes difficulty but has workarounds
- Minor: annoyances that reduce usability

**WCAG Conformance**: DOES NOT CONFORM / PARTIALLY CONFORMS / CONFORMS
**Assistive Technology Compatibility**: FAIL / PARTIAL / PASS

## Issues Found
### Issue N: [title]
**WCAG Criterion**: [number - name] (Level A/AA/AAA)
**Severity**: Critical / Serious / Moderate / Minor
**User Impact**: [who is affected and how]
**Location**: [page, component, element]
**Evidence**: [screenshot, screen reader transcript, code snippet]
**Recommended Fix**: [concrete change]
**Testing Verification**: [how to confirm the fix works]

## Remediation Priority
### Immediate (Critical/Serious - fix before release)
### Short-term (Moderate - fix within next sprint)
### Ongoing (Minor - address in regular maintenance)
```

## Screen Reader Testing Protocol

- Navigation: heading structure logical/hierarchical (h1 -> h2 -> h3); landmark
  regions present and labeled; skip links present; tab order logical; focus
  indicator always visible
- Interactive components: buttons announce role/label and state changes; links
  distinguishable from buttons with clear destination; forms have associated
  labels, announced required fields and errors; modals trap focus, Escape
  closes, focus returns on close; custom widgets (tabs, accordions, menus) use
  proper ARIA roles and keyboard patterns
- Dynamic content: live regions announce status without focus change; loading
  states communicated; errors announced immediately and associated with the
  field; toasts announced via `aria-live` and dismissible

## Keyboard Navigation Audit Checklist

- All interactive elements reachable via Tab, in an order that follows visual layout
- Skip navigation link present and functional; no keyboard traps
- Focus indicator visible on every interactive element
- Escape closes modals, dropdowns, overlays; focus returns to the trigger element
- Tabs: Tab moves into/out of the tablist, arrow keys move between tabs, Home/End
  jump to first/last, `aria-selected` marks the active tab
- Menus: arrow keys navigate items, Enter/Space activates, Escape closes and
  returns focus to trigger
- Carousels/sliders: arrow keys move slides, pause/stop control is keyboard
  accessible, current position announced
- Data tables: headers associated via `scope`/`headers`, caption or `aria-label`
  describes purpose, sortable columns operable via keyboard

## Automated Baseline Scan

```bash
npx @axe-core/cli http://localhost:8000 --tags wcag2a,wcag2aa,wcag22aa
npx lighthouse http://localhost:8000 --only-categories=accessibility --output=json
```

Automated tools catch roughly 30% of accessibility issues - manual assistive
technology testing catches the rest: keyboard-only journeys, screen reader
journeys, 200%/400% zoom, reduced motion, high contrast/forced colors.

## Legal and Regulatory Reference

ADA Title III, European Accessibility Act (EAA) / EN 301 549, Section 508 for
government/government-funded projects. Note applicability, do not assume.
