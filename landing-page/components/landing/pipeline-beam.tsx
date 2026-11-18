"use client";

import { FileCheck2, Languages, Link2, ScanText, Zap } from "lucide-react";
import { useInView } from "motion/react";
import { useRef } from "react";
import { PIPELINE_STEPS } from "./data";

const ICONS = { link: Link2, zap: Zap, scan: ScanText, languages: Languages, file: FileCheck2 } as const;

/** One connector, drawn twice: across for wide screens, down for narrow ones. The beam only runs while the diagram is on screen. */
function Connector({ index }: { index: number }) {
  const delay = { animationDelay: `${index * 0.34}s` };
  return (
    <span className="pipe-link" aria-hidden="true">
      <svg className="pipe-link-h" viewBox="0 0 100 12" preserveAspectRatio="none">
        <path className="pipe-base" d="M0 6 H100" pathLength={100} vectorEffect="non-scaling-stroke" />
        <path className="pipe-beam" d="M0 6 H100" pathLength={100} vectorEffect="non-scaling-stroke" style={delay} />
      </svg>
      <svg className="pipe-link-v" viewBox="0 0 12 100" preserveAspectRatio="none">
        <path className="pipe-base" d="M6 0 V100" pathLength={100} vectorEffect="non-scaling-stroke" />
        <path className="pipe-beam" d="M6 0 V100" pathLength={100} vectorEffect="non-scaling-stroke" style={delay} />
      </svg>
    </span>
  );
}

export function PipelineBeam() {
  const ref = useRef<HTMLOListElement>(null);
  const live = useInView(ref, { margin: "0px 0px -10% 0px" });

  return (
    <ol ref={ref} className={`pipe ${live ? "is-live" : ""}`}>
      {PIPELINE_STEPS.map((step, i) => {
        const Icon = ICONS[step.icon];
        const last = i === PIPELINE_STEPS.length - 1;
        return (
          <li key={step.title} className={`pipe-step ${last ? "is-last" : ""}`}>
            <span className="pipe-node">
              <Icon size={22} aria-hidden="true" />
              <span className="pipe-num">{String(i + 1).padStart(2, "0")}</span>
            </span>
            <h3>{step.title}</h3>
            <p>{step.body}</p>
            {last ? null : <Connector index={i} />}
          </li>
        );
      })}
    </ol>
  );
}
