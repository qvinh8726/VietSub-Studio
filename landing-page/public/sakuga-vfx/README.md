# Sakuga VFX (vendored, locally adapted)

The upstream `tabs.js` core originates from [finktheartist/sakuga-vfx](https://github.com/finktheartist/sakuga-vfx), commit `c3dabb173928a3ad2372183103caf2e6f50953d0`, under the included MIT license.

## Landing-page adaptations for v2.0.1

- `retain()` / `release()` explicitly manage resize, visibility and reduced-motion subscriptions.
- The shared requestAnimationFrame handle is retained and cancelled on release, when hidden, or when reduced motion is enabled. Active finite effects and timers are cleared at those boundaries.
- The landing page does **not** instantiate the upstream custom tabs or permanently ticking `Base` elements. Native React tabs use a shared-layout spring pill; Sakuga only draws a small finite smoke puff.
- The CTA is a real external anchor. `motion/react` supplies squash/overshoot, while the vendored core supplies warm burst/slash strokes.
- The palette is applied in `components/landing/sakuga.tsx`. The core is released on component unmount, and its canvas is removed.

Keep these lifecycle adaptations when updating the upstream core. `README-tabs.md` retains the original element's usage note; the production landing page does not use that element.
