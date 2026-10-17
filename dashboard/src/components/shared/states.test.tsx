import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ExpiredLinkPage } from "@/app/ExpiredLinkPage";
import { NotFoundPage } from "@/app/NotFoundPage";
import { DocsLink } from "@/components/shared/DocsLink";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { NotFound } from "@/components/shared/NotFound";
import { PageHeader, SectionHead } from "@/components/shared/PageHeader";
import { StatCard } from "@/components/shared/StatCard";
import { Skeleton } from "@/components/ui/skeleton";
import { docsUrl } from "@/config/docs-links";
import { ApiError, NetworkError } from "@/lib/api-client";
import { renderWithApp } from "@/test/utils/render";

describe("loading state", () => {
  it("renders a skeleton that assistive tech skips", () => {
    const { container } = render(<Skeleton className="h-4 w-20" />);

    expect(container.firstElementChild).toHaveAttribute("aria-hidden", "true");
    expect(container.firstElementChild).toHaveClass("animate-pulse");
  });
});

describe("empty state", () => {
  it("says what is empty and why", () => {
    render(<EmptyState title="Nothing is waiting for you.">No run is parked at an approval.</EmptyState>);

    expect(screen.getByText("Nothing is waiting for you.")).toBeInTheDocument();
    expect(screen.getByText(/No run is parked at an approval\./)).toBeInTheDocument();
  });
});

describe("error state", () => {
  it("shows the server message of an API error and retries on click", async () => {
    const onRetry = vi.fn();
    render(<ErrorState error={new ApiError(500, { message: "Internal error", code: "internal" })} onRetry={onRetry} />);

    expect(screen.getByRole("alert")).toHaveTextContent("Internal error");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("shows a transient notice for a 503", () => {
    render(<ErrorState error={new ApiError(503, { message: "locked", code: "unavailable" })} onRetry={vi.fn()} />);

    expect(screen.getByRole("alert")).toHaveTextContent("temporarily unavailable");
    expect(screen.getByRole("alert")).not.toHaveTextContent("locked");
  });

  it("says the server cannot be reached for a network error", () => {
    render(<ErrorState error={new NetworkError("Failed to fetch")} onRetry={vi.fn()} />);

    expect(screen.getByRole("alert")).toHaveTextContent("cannot be reached");
  });

  it("falls back to a generic message for an unknown error", () => {
    render(<ErrorState error={new Error("boom")} onRetry={vi.fn()} />);

    expect(screen.getByRole("alert")).toHaveTextContent("Something went wrong");
    expect(screen.getByRole("alert")).not.toHaveTextContent("boom");
  });
});

describe("404 page", () => {
  it("explains the missing page and links back to Overview", async () => {
    await renderWithApp(<NotFoundPage />);

    expect(screen.getByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to Overview" })).toHaveAttribute("href", "/");
  });

  it("takes a custom title and message for an unknown id", async () => {
    await renderWithApp(<NotFound title="Task not found" message="There is no task-9999." />);

    expect(screen.getByRole("heading", { name: "Task not found" })).toBeInTheDocument();
    expect(screen.getByText("There is no task-9999.")).toBeInTheDocument();
  });
});

describe("expired-link page", () => {
  it("tells the user to run the command again", () => {
    render(<ExpiredLinkPage />);

    expect(screen.getByRole("alert")).toHaveTextContent("This dashboard link has expired");
    expect(screen.getByText("Run /agentry:dashboard again to get a fresh link.")).toBeInTheDocument();
  });
});

describe("docs link", () => {
  it("opens the page's documentation in a new tab", () => {
    render(<DocsLink page="needs" />);

    const link = screen.getByRole("link", { name: /Learn more in the docs/ });
    expect(link).toHaveAttribute("href", "https://github.com/AlexWaha/agentry/blob/main/docs/pipeline.md#how-a-checkpoint-works");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("leaves the anchor off when the entry has none", () => {
    expect(docsUrl("overview")).toBe("https://github.com/AlexWaha/agentry/blob/main/docs/pipeline.md");
  });
});

describe("page header, section head and stat card", () => {
  it("renders the title, the subtitle and the docs link", () => {
    render(<PageHeader title="Metrics" subtitle="Gate timings" docsPage="metrics" />);

    expect(screen.getByRole("heading", { level: 1, name: "Metrics" })).toBeInTheDocument();
    expect(screen.getByText("Gate timings")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Learn more/ })).toBeInTheDocument();
  });

  it("renders a section heading with a right-hand note", () => {
    render(<SectionHead title="Recent events" right="5 newest" />);

    expect(screen.getByRole("heading", { level: 2, name: "Recent events" })).toBeInTheDocument();
    expect(screen.getByText("5 newest")).toBeInTheDocument();
  });

  it("renders a stat with its label, value and hint, and tints the attention tone", () => {
    render(<StatCard label="Needs you" value={3} hint={<span>oldest 2h</span>} tone="attention" />);

    expect(screen.getByText("Needs you")).toBeInTheDocument();
    expect(screen.getByText("3")).toHaveClass("text-attention");
    expect(screen.getByText("oldest 2h")).toBeInTheDocument();
  });
});
