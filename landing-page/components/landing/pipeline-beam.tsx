"use client";

import { Clapperboard, Radio, ScanText } from "lucide-react";
import { useInView } from "motion/react";
import { useRef } from "react";
import { PIPELINE_STEPS } from "./data";
import { Reveal } from "./reveal";

const ICONS = { scan: ScanText, edit: Clapperboard, publish: Radio };

export function PipelineBeam() {
  const ref = useRef<HTMLDivElement>(null);
  const live = useInView(ref, { margin: "0px 0px -10% 0px" });
  return (
    <div ref={ref} className={`liquid-workflow ${live ? "is-live" : ""}`}>
      <svg
        className="liquid-path horizontal-path"
        viewBox="0 0 1100 180"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <defs>
          <linearGradient id="liquid-gradient">
            <stop stopColor="#f5c542" />
            <stop offset="0.5" stopColor="#ff9c69" />
            <stop offset="1" stopColor="#7ee0b8" />
          </linearGradient>
        </defs>
        <path
          d="M 165 80 C 310 -10, 390 170, 550 80 S 810 -10, 935 80"
          className="liquid-base"
        />
        <path
          d="M 165 80 C 310 -10, 390 170, 550 80 S 810 -10, 935 80"
          className="liquid-beam"
          pathLength="100"
        />
      </svg>
      <svg
        className="liquid-path vertical-path"
        viewBox="0 0 80 850"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <path
          d="M 40 50 C 80 160, 0 250, 40 340 S 80 530, 40 620 S 0 730, 40 800"
          className="liquid-base"
        />
        <path
          d="M 40 50 C 80 160, 0 250, 40 340 S 80 530, 40 620 S 0 730, 40 800"
          className="liquid-beam"
          pathLength="100"
        />
      </svg>
      <ol className="workflow-stages">
        {PIPELINE_STEPS.map((step, i) => {
          const Icon = ICONS[step.icon];
          return (
            <li key={step.label}>
              <Reveal
                className={`workflow-stage stage-${i + 1}`}
                delay={i * 0.1}
              >
                <div className="workflow-node">
                  <Icon size={26} strokeWidth={1.5} aria-hidden="true" />
                  <span className="workflow-number">0{i + 1}</span>
                </div>
                <div className="workflow-copy">
                  <p className="workflow-label">{step.label}</p>
                  <h3>{step.title}</h3>
                  <p>{step.body}</p>
                  <span className="workflow-detail">{step.note}</span>
                </div>
              </Reveal>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
