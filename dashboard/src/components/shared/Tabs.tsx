import { useId, useRef, type KeyboardEvent, type ReactNode } from "react";

import { cn } from "@/lib/utils";

export interface TabItem<T extends string> {
  value: T;
  label: string;
  /** Shown after the label, for example the number of items in the tab. */
  count?: number;
}

export interface TabsProps<T extends string> {
  tabs: readonly TabItem<T>[];
  value: T;
  onChange: (value: T) => void;
  /** Accessible name of the tab list. */
  label: string;
  /** Prefix for the tab and panel ids; defaults to a generated one. Pass the same value to TabPanel. */
  idBase?: string;
  className?: string;
}

export const tabId = (idBase: string, value: string): string => `${idBase}-tab-${value}`;
export const panelId = (idBase: string, value: string): string => `${idBase}-panel-${value}`;

/**
 * A tablist with roving focus (FR-127): `aria-selected` on the current tab, Left and Right arrows (and Home
 * and End) move between tabs and select them. The bar scrolls inside itself on narrow widths, never the page.
 */
export function Tabs<T extends string>({ tabs, value, onChange, label, idBase, className }: TabsProps<T>) {
  const generated = useId();
  const base = idBase ?? generated;
  const buttons = useRef<Record<string, HTMLButtonElement | null>>({});

  const select = (item: TabItem<T>): void => {
    onChange(item.value);
    buttons.current[item.value]?.focus();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number): void => {
    const last = tabs.length - 1;
    const target =
      event.key === "ArrowRight" ? (index === last ? 0 : index + 1)
      : event.key === "ArrowLeft" ? (index === 0 ? last : index - 1)
      : event.key === "Home" ? 0
      : event.key === "End" ? last
      : -1;
    if (target < 0) return;
    event.preventDefault();
    select(tabs[target]);
  };

  return (
    <div className={cn("-mx-0.5 overflow-x-auto p-0.5", className)}>
      <div role="tablist" aria-label={label} className="inline-flex gap-0.5 rounded-lg border border-border-control bg-muted p-0.5">
        {tabs.map((item, index) => {
          const selected = item.value === value;
          return (
            <button
              key={item.value}
              ref={(node) => {
                buttons.current[item.value] = node;
              }}
              type="button"
              role="tab"
              id={tabId(base, item.value)}
              aria-selected={selected}
              aria-controls={panelId(base, item.value)}
              tabIndex={selected ? 0 : -1}
              onClick={() => onChange(item.value)}
              onKeyDown={(event) => onKeyDown(event, index)}
              className={cn(
                "rounded-md px-3 py-1 text-[12.5px] whitespace-nowrap text-muted-foreground hover:text-foreground",
                selected && "bg-background font-semibold text-foreground ring-1 ring-border-strong",
              )}
            >
              {item.label}
              {item.count === undefined ? null : (
                <>
                  {" "}
                  <span className="ml-0.5 font-mono text-[11px] opacity-80">{item.count}</span>
                </>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export interface TabPanelProps {
  idBase: string;
  value: string;
  children: ReactNode;
  className?: string;
}

/** The panel of the selected tab. Render it only for the current value. */
export function TabPanel({ idBase, value, children, className }: TabPanelProps) {
  return (
    <div role="tabpanel" id={panelId(idBase, value)} aria-labelledby={tabId(idBase, value)} tabIndex={0} className={className}>
      {children}
    </div>
  );
}
