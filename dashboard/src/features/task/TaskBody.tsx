import { Card } from "@/components/ui/card";
import { SectionHead } from "@/components/shared/PageHeader";
import { t } from "@/i18n";
import { renderMarkdown, splitSections, type MarkdownSection } from "@/lib/markdown";

/** The order of the well-known sections (FR-96); any other section follows in file order. */
const SECTION_ORDER: readonly RegExp[] = [
  /^description\b/i,
  /^acceptance criteria\b/i,
  /^definition of done\b/i,
  /^(backend dependencies|frontend impact)\b/i,
  /^deploy actions\b/i,
  /^notes\b/i,
];

const COMMENT_LINE = /^\s*<!--.*-->\s*$/;

/** A heading without its trailing parenthetical; no regex, so a long run of spaces costs linear time. */
function titleOf(heading: string): string {
  const open = heading.indexOf("(");
  return (open === -1 ? heading : heading.slice(0, open)).trimEnd();
}

/** Sections with content, in display order. A heading's parenthetical ("Acceptance Criteria (spec trace)") is dropped from the title. */
export function orderedSections(body: string): MarkdownSection[] {
  const sections = splitSections(body)
    .map((section) => ({
      title: titleOf(section.title),
      lines: section.lines.filter((line) => !COMMENT_LINE.test(line)),
    }))
    .filter((section) => section.title && section.lines.some((line) => line.trim()));
  const known = SECTION_ORDER.flatMap((pattern) => sections.filter((section) => pattern.test(section.title)));
  return [...known, ...sections.filter((section) => !known.includes(section))];
}

function BodySection({ section }: { section: MarkdownSection }) {
  const { nodes, checked, total } = renderMarkdown(section.lines);
  return (
    <section className="mt-6">
      <SectionHead title={section.title} right={total > 0 ? <span>{t("task.progress", { checked, total })}</span> : undefined} />
      <Card className="grid gap-2.5 px-4 py-3.5 text-sm leading-relaxed">{nodes}</Card>
    </section>
  );
}

/** The markdown body of a task as one card per `## ` section; absent sections render nothing. */
export function TaskBody({ body }: { body: string }) {
  return (
    <>
      {orderedSections(body).map((section, index) => (
        <BodySection key={`${index}:${section.title}`} section={section} />
      ))}
    </>
  );
}
