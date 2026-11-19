"use client";

import Script from "next/script";
import { motion, useAnimationControls } from "motion/react";
import { useEffect, useSyncExternalStore } from "react";
import type { ReactNode } from "react";
import { usePrefersReducedMotion } from "./use-reduced-motion";

const READY_EVENT = "vs:sakuga-ready";

function onScriptReady() {
  const fx = window.SakugaFX;
  if (!fx) return;
  // Warm ink and translucent sage smoke, never the library's default hard purple.
  Object.assign(fx.P, {
    fill: "#dbe9cb",
    shade: "#aabf92",
    line: "#6c806158",
    accent: "#ffb879",
    ink: "#f5c542",
  });
  fx.P.sfx = ["#f5c542", "#ffb879"];
  fx.retain?.();
  window.dispatchEvent(new Event(READY_EVENT));
}

/** The core only runs its clock while a short-lived effect is visible. */
export function SakugaScript() {
  useEffect(() => {
    window.SakugaFX?.retain?.();
    return () => window.SakugaFX?.release?.();
  }, []);
  return (
    <Script
      src="/sakuga-vfx/tabs.js"
      strategy="afterInteractive"
      onReady={onScriptReady}
    />
  );
}

function subscribe(onChange: () => void) {
  window.addEventListener(READY_EVENT, onChange);
  return () => window.removeEventListener(READY_EVENT, onChange);
}

export function useSakugaReady() {
  return useSyncExternalStore(
    subscribe,
    () => Boolean(window.SakugaFX),
    () => false,
  );
}

type SakugaCtaProps = {
  href: string;
  children: ReactNode;
  className?: string;
  ariaLabel?: string;
};

/** A real download link, with a little tactile squash and a warm hand-drawn slash. */
export function SakugaCta({
  href,
  children,
  className = "",
  ariaLabel,
}: SakugaCtaProps) {
  const reduced = usePrefersReducedMotion();
  const controls = useAnimationControls();
  function impact(event: React.MouseEvent<HTMLAnchorElement>) {
    const fx = window.SakugaFX;
    if (reduced || !fx || fx.calm) return;
    const p = fx.at(event.currentTarget);
    fx.burst(p.x + p.w * 0.32, p.y, { r: 32, spikes: 9, life: 0.3 });
    fx.slash(p.x + p.w * 0.26, p.y - 4, {
      len: 72,
      ang: -0.46,
      thick: 5,
      life: 0.36,
      col: "#ffb879",
    });
    fx.puff(p.x - p.w / 2, p.y + 10, {
      n: 3,
      size: 6,
      vx: -12,
      vy: -8,
      life: 0.45,
    });
    void controls.start({
      scaleX: [1.035, 0.98, 1],
      scaleY: [0.95, 1.035, 1],
      transition: { duration: 0.48, ease: "easeOut" },
    });
  }
  return (
    <motion.a
      href={href}
      target="_blank"
      rel="noreferrer"
      className={`cta-sun ${className}`}
      aria-label={ariaLabel}
      animate={controls}
      whileHover={reduced ? undefined : { y: -2 }}
      whileTap={reduced ? undefined : { scaleX: 1.025, scaleY: 0.94 }}
      transition={{ type: "spring", stiffness: 260, damping: 17 }}
      onClick={impact}
    >
      {children}
    </motion.a>
  );
}
