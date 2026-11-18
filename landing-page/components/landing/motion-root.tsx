"use client";

import { MotionConfig } from "motion/react";
import type { ReactNode } from "react";

/** Every motion animation below follows the visitor's reduced-motion setting. */
export function MotionRoot({ children }: { children: ReactNode }) {
  return <MotionConfig reducedMotion="user">{children}</MotionConfig>;
}
