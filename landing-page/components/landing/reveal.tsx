"use client";

import { motion } from "motion/react";
import type { ReactNode } from "react";

type RevealProps = { children: ReactNode; className?: string; delay?: number };

/** Fades a block up once when it scrolls into view. MotionConfig reducedMotion="user" drops the movement. */
export function Reveal({ children, className, delay = 0 }: RevealProps) {
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y: 18 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.3 }}
      transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1], delay }}
    >
      {children}
    </motion.div>
  );
}
