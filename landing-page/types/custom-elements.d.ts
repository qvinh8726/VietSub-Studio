// Types for the vendored Sakuga VFX core (public/sakuga-vfx/tabs.js) and the custom elements the page uses.
import type { DetailedHTMLProps, HTMLAttributes } from "react";

type Point = { x: number; y: number; w: number; h: number };

export interface SakugaSpring {
  x: number;
  v: number;
  to: number;
  step(dt: number): number;
}

export interface SakugaSquash {
  s: SakugaSpring;
  pop: SakugaSpring;
  tilt: SakugaSpring;
  step(dt: number): void;
}

export interface SakugaLayer {
  c: HTMLCanvasElement;
}

export interface SakugaBase extends HTMLElement {
  readonly root: ShadowRoot;
  $(selector: string): Element | null;
  backLayer(): SakugaLayer;
  emit(type: string, detail?: unknown): void;
}

export interface SakugaApi {
  version: number;
  paused: boolean;
  retain?(): void;
  release?(): void;
  readonly P: {
    fill: string;
    shade: string;
    line: string;
    accent: string;
    ink: string;
    sfx: [string, string];
  };
  readonly calm: boolean;
  rnd(a?: number, b?: number): number;
  Spring: new (x?: number, k?: number, d?: number) => SakugaSpring;
  later(ms: number, fn: () => void): void;
  puff(
    x: number,
    y: number,
    o?: {
      n?: number;
      size?: number;
      vx?: number;
      vy?: number;
      life?: number;
      spread?: number;
    },
  ): void;
  burst(
    x: number,
    y: number,
    o?: { r?: number; spikes?: number; life?: number; layer?: SakugaLayer },
  ): void;
  slash(
    x: number,
    y: number,
    o?: {
      len?: number;
      ang?: number;
      life?: number;
      thick?: number;
      col?: string;
    },
  ): void;
  lines(
    x: number,
    y: number,
    o?: {
      n?: number;
      dir?: number;
      len?: number;
      spread?: number;
      life?: number;
    },
  ): void;
  ticks(
    x: number,
    y: number,
    o?: {
      n?: number;
      r?: number;
      len?: number;
      dir?: number;
      life?: number;
      fan?: number;
    },
  ): void;
  wob(
    x: number,
    y: number,
    o?: { dir?: number; r?: number; life?: number },
  ): void;
  at(el: Element, fx?: number, fy?: number): Point;
  squashy(
    el: HTMLElement,
    o?: { low?: number; k?: number; d?: number; wide?: number },
  ): SakugaSquash;
  Base: { new (): SakugaBase; prototype: SakugaBase };
  define(tag: string, cls: CustomElementConstructor): void;
}

type ElementProps<P = object> = DetailedHTMLProps<
  HTMLAttributes<HTMLElement>,
  HTMLElement
> &
  P;

declare global {
  interface Window {
    SakugaFX?: SakugaApi;
  }
}

declare module "react" {
  namespace JSX {
    interface IntrinsicElements {
      "sakuga-tabs": ElementProps<{ label?: string; selected?: number }>;
      "vs-sakuga-link": ElementProps;
    }
  }
}
