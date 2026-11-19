"use client";

import { motion } from "motion/react";
import type { ReactNode } from "react";
import { usePrefersReducedMotion } from "./use-reduced-motion";

export function Reveal({
  children,
  className,
  delay = 0,
}: {
  children: ReactNode;
  className?: string;
  delay?: number;
}) {
  const reduced = usePrefersReducedMotion();
  return (
    <motion.div
      className={`soft-reveal ${className ?? ""}`}
      initial={{ opacity: 0, y: 22, filter: "blur(7px)" }}
      whileInView={{ opacity: 1, y: 0, filter: "blur(0px)" }}
      viewport={{ once: true, amount: 0.14 }}
      transition={
        reduced
          ? { duration: 0 }
          : { type: "spring", stiffness: 75, damping: 19, mass: 0.9, delay }
      }
    >
      {children}
    </motion.div>
  );
}
