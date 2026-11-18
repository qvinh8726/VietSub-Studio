"use client";

import Script from "next/script";
import { useSyncExternalStore } from "react";
import type { ReactNode } from "react";
import type { SakugaApi, SakugaLayer, SakugaSquash } from "@/types/custom-elements";

const READY_EVENT = "vs:sakuga-ready";

/** The effects palette: paper fill, sun shade, dark ink lines and a vermilion accent, matching globals.css. */
function themeEffects(fx: SakugaApi) {
  Object.assign(fx.P, { fill: "#fff6dc", shade: "#f5c542", line: "#1a1408", accent: "#ff6b3d", ink: "#1a1408" });
  fx.P.sfx = ["#1a1408", "#fff6dc"];
}

/**
 * <vs-sakuga-link> wraps a real <a> in its light DOM, so the link works and reads as a link before the script loads.
 * Once defined it squashes the link while pressed, overshoots on release and lands an impact on click.
 */
function defineSakugaLink(fx: SakugaApi) {
  if (customElements.get("vs-sakuga-link")) return;

  class VsSakugaLink extends fx.Base {
    static css = ":host{display:inline-flex;vertical-align:middle}";
    private link: HTMLAnchorElement | null = null;
    private squash: SakugaSquash | null = null;
    private back: SakugaLayer | null = null;

    render() {
      return "<slot></slot>";
    }

    setup() {
      this.link = this.querySelector("a");
      if (!this.link) return;
      this.back = this.backLayer();
      if (!fx.calm) this.squash = fx.squashy(this.link, { low: 0.72, wide: 0.32 });
      this.link.addEventListener("pointerdown", () => {
        if (fx.calm || !this.link) return;
        const c = fx.at(this.link);
        fx.ticks(c.x, c.y - c.h / 2 - 8, { dir: -Math.PI / 2, n: 3, r: 6, len: 12, fan: 0.55, life: 0.35 });
      });
      this.link.addEventListener("click", () => this.impact());
    }

    impact() {
      const link = this.link;
      if (fx.calm || !link) return;
      const c = fx.at(link);
      link.classList.add("is-impact");
      fx.later(170, () => link.classList.remove("is-impact"));
      if (this.back) fx.burst(c.x, c.y, { r: Math.min(100, Math.max(64, c.w * 0.32)), spikes: 13, life: 0.38, layer: this.back });
      fx.slash(c.x + c.w * 0.08, c.y - 2, { len: Math.min(170, c.w * 0.6), ang: -0.42, thick: 13, life: 0.42 });
      fx.puff(c.x - c.w / 2 - 8, c.y + 6, { size: 11, n: 4, vx: -26, vy: -10, life: 0.7 });
      fx.puff(c.x + c.w / 2 + 8, c.y + 6, { size: 11, n: 4, vx: 26, vy: -10, life: 0.7 });
      if (this.squash) {
        this.squash.pop.x = 1.14;
        this.squash.tilt.x = -2.5;
      }
      fx.later(210, () => {
        fx.wob(c.x + c.w / 2 + 4, c.y, { dir: 1, r: 14 });
        fx.wob(c.x - c.w / 2 - 4, c.y, { dir: -1, r: 14 });
      });
    }

    frame(dt: number) {
      this.squash?.step(dt);
    }
  }

  fx.define("vs-sakuga-link", VsSakugaLink as unknown as CustomElementConstructor);
}

function onScriptReady() {
  const fx = window.SakugaFX;
  if (!fx) return;
  themeEffects(fx);
  defineSakugaLink(fx);
  window.dispatchEvent(new Event(READY_EVENT));
}

/** Loads the vendored Sakuga core once per page. Effects stay off for reduced motion inside the core itself. */
export function SakugaScript() {
  return <Script src="/sakuga-vfx/tabs.js" strategy="afterInteractive" onReady={onScriptReady} />;
}

function subscribe(onChange: () => void) {
  window.addEventListener(READY_EVENT, onChange);
  return () => window.removeEventListener(READY_EVENT, onChange);
}

const getSnapshot = () => Boolean(window.SakugaFX) && Boolean(customElements.get("sakuga-tabs"));
const getServerSnapshot = () => false;

/** True once the Sakuga script has run and its elements are defined. */
export function useSakugaReady() {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

type SakugaCtaProps = {
  href: string;
  children: ReactNode;
  className?: string;
  ariaLabel?: string;
};

/** The main download link. Without JavaScript it is a plain link that opens the release page. */
export function SakugaCta({ href, children, className = "", ariaLabel }: SakugaCtaProps) {
  return (
    <vs-sakuga-link className="sakuga-link">
      <a href={href} target="_blank" rel="noreferrer" className={`cta-sun ${className}`} aria-label={ariaLabel}>
        {children}
      </a>
    </vs-sakuga-link>
  );
}
