"use client";

import { Pause, Play } from "lucide-react";
import { useState } from "react";
import type { MarqueeItem } from "./data";

function Item({ item }: { item: MarqueeItem }) {
  return (
    <li className={`mq-item ${item.note ? "is-muted" : ""}`}>
      {item.han ? (
        <span className="mq-han" lang="zh-CN">
          {item.han}
        </span>
      ) : null}
      <span>{item.label}</span>
      {item.note ? <span className="mq-note">{item.note}</span> : null}
    </li>
  );
}

/**
 * One row of the marquee. The list is drawn twice so the loop has no seam; the copy is hidden from screen readers.
 * CSS pauses it on hover or focus and stops it for reduced motion (see .mq in globals.css).
 */
function Row({
  items,
  label,
  reverse,
}: {
  items: MarqueeItem[];
  label: string;
  reverse?: boolean;
}) {
  return (
    <div className={`mq-row ${reverse ? "is-reverse" : ""}`}>
      <div className="mq-track">
        <ul aria-label={label}>
          {items.map((item) => (
            <Item key={item.label} item={item} />
          ))}
        </ul>
        <ul aria-hidden="true" className="mq-copy">
          {items.map((item) => (
            <Item key={item.label} item={item} />
          ))}
        </ul>
      </div>
    </div>
  );
}

export function Marquee({
  platforms,
  tech,
}: {
  platforms: MarqueeItem[];
  tech: MarqueeItem[];
}) {
  const [paused, setPaused] = useState(false);
  return (
    <div className={`mq ${paused ? "is-paused" : ""}`}>
      <Row
        items={platforms}
        label="Nguồn video và kênh xuất bản; Kuaishou chỉ là nguồn"
      />
      <Row items={tech} label="Công nghệ và chuẩn" reverse />
      <button
        type="button"
        className="mq-pause"
        aria-pressed={paused}
        onClick={() => setPaused(!paused)}
      >
        {paused ? (
          <Play size={12} aria-hidden="true" />
        ) : (
          <Pause size={12} aria-hidden="true" />
        )}
        {paused ? "Tiếp tục chuyển động" : "Tạm dừng chuyển động"}
      </button>
    </div>
  );
}
