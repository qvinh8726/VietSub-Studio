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
function Row({ items, label, reverse }: { items: MarqueeItem[]; label: string; reverse?: boolean }) {
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

export function Marquee({ platforms, tech }: { platforms: MarqueeItem[]; tech: MarqueeItem[] }) {
  return (
    <div className="mq">
      <Row items={platforms} label="Nguồn video" />
      <Row items={tech} label="Công nghệ và chuẩn" reverse />
    </div>
  );
}
