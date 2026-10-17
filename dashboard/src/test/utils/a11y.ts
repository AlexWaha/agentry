import axe from "axe-core";

/**
 * Runs axe-core on an element and throws with a readable list when it finds a violation (FR-91). The
 * colour-contrast rule is off because jsdom has no layout or computed colours; contrast is judged on the
 * screenshots of the UI evidence instead.
 */
export async function expectNoAxeViolations(element: Element): Promise<void> {
  const results = await axe.run(element, { rules: { "color-contrast": { enabled: false } } });
  if (results.violations.length === 0) return;
  const lines = results.violations.map(
    (violation) => `${violation.id}: ${violation.help} (${violation.nodes.map((node) => node.target.join(" ")).join(", ")})`,
  );
  throw new Error(`axe found ${results.violations.length} violation(s):\n${lines.join("\n")}`);
}
