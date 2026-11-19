"use client";

import { motion, useScroll, useTransform } from "motion/react";
import { usePrefersReducedMotion } from "./use-reduced-motion";

/** Passive, scroll-linked depth; motion owns and cleans up its subscriptions. */
export function Atmosphere() {
  const reduced = usePrefersReducedMotion();
  const { scrollY } = useScroll();
  const y = useTransform(scrollY, [0, 1200], [0, 150]);
  return (
    <div className="atmosphere" aria-hidden="true">
      <motion.div className="ambient-mesh" style={{ y: reduced ? 0 : y }}>
        <span className="aurora aurora-sun" />
        <span className="aurora aurora-mint" />
        <span className="aurora aurora-coral" />
      </motion.div>
      <div className="film-grain" />
      <span className="ambient-star star-one">✧</span>
      <span className="ambient-star star-two">✧</span>
      <span className="ambient-orbit" />
    </div>
  );
}
