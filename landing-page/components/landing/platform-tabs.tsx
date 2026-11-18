"use client";

import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { useSakugaReady } from "./sakuga";

type Tab = { id: string; label: string; han: string };

type PlatformTabsProps = {
  tabs: Tab[];
  index: number;
  onChange: (index: number) => void;
  label: string;
};

type SakugaTabsElement = HTMLElement & { selected: number };

/**
 * Source tabs. Renders <sakuga-tabs> (slide, stretch and a puff of smoke on landing) once the script is ready,
 * and a plain ARIA tablist with the same keys before that or without JavaScript.
 */
export function PlatformTabs({ tabs, index, onChange, label }: PlatformTabsProps) {
  const ready = useSakugaReady();
  const ref = useRef<HTMLElement>(null);
  // The element reads this once. Later changes go through the effect below, so a tab the visitor picked keeps its slide.
  const [initialIndex] = useState(index);

  useEffect(() => {
    const el = ref.current as SakugaTabsElement | null;
    if (!ready || !el) return;
    const handle = (event: Event) => {
      const detail = (event as CustomEvent<{ index: number }>).detail;
      if (detail && typeof detail.index === "number") onChange(detail.index);
    };
    el.addEventListener("change", handle);
    return () => el.removeEventListener("change", handle);
  }, [ready, onChange]);

  useEffect(() => {
    const el = ref.current as SakugaTabsElement | null;
    if (ready && el && el.selected !== index) el.selected = index;
  }, [ready, index]);

  if (ready) {
    return (
      <sakuga-tabs ref={ref} className="platform-tabs" label={label} selected={initialIndex}>
        {tabs.map((tab) => (
          <option key={tab.id} value={tab.id}>
            {`${tab.han} ${tab.label}`}
          </option>
        ))}
      </sakuga-tabs>
    );
  }

  return <FallbackTabs tabs={tabs} index={index} onChange={onChange} label={label} />;
}

function FallbackTabs({ tabs, index, onChange, label }: PlatformTabsProps) {
  const listRef = useRef<HTMLDivElement>(null);

  const move = (event: KeyboardEvent<HTMLButtonElement>, current: number) => {
    const keys: Record<string, number> = { ArrowRight: current + 1, ArrowLeft: current - 1, Home: 0, End: tabs.length - 1 };
    if (!(event.key in keys)) return;
    event.preventDefault();
    const next = (keys[event.key] + tabs.length) % tabs.length;
    onChange(next);
    listRef.current?.querySelectorAll<HTMLButtonElement>("[role=tab]")[next]?.focus();
  };

  return (
    <div ref={listRef} className="platform-tabs-fallback" role="tablist" aria-label={label}>
      {tabs.map((tab, i) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          aria-selected={i === index}
          tabIndex={i === index ? 0 : -1}
          onClick={() => onChange(i)}
          onKeyDown={(event) => move(event, i)}
        >
          <span lang="zh-CN">{tab.han}</span> {tab.label}
        </button>
      ))}
    </div>
  );
}
