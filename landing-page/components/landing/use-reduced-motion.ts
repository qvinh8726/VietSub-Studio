"use client";

import { useSyncExternalStore } from "react";

const QUERY = "(prefers-reduced-motion: reduce)";

function subscribe(onChange: () => void) {
  const media = matchMedia(QUERY);
  media.addEventListener("change", onChange);
  return () => media.removeEventListener("change", onChange);
}

/**
 * The reduced-motion setting, hydration-safe: the server and the first client render both see `false`,
 * then React re-renders with the real value. motion's useReducedMotion reads the media query during the
 * first render, which makes the HTML differ from the server's.
 */
export function usePrefersReducedMotion() {
  return useSyncExternalStore(
    subscribe,
    () => matchMedia(QUERY).matches,
    () => false,
  );
}
