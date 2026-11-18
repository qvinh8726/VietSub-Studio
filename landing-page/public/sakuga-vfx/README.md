# Sakuga VFX (vendored)

`tabs.js` is copied unmodified from [finktheartist/sakuga-vfx](https://github.com/finktheartist/sakuga-vfx)
at commit `c3dabb173928a3ad2372183103caf2e6f50953d0`, under the MIT license in `LICENSE`.

The file holds the `<sakuga-tabs>` element plus the shared core, which sets `window.SakugaFX`.
The landing page loads it once with `next/script` and builds on the core API:

- `components/landing/sakuga.tsx` sets the effect palette (`SakugaFX.P`) and defines
  `<vs-sakuga-link>`, the download link with squash, overshoot, burst and slash.
- `app/globals.css` overrides the `--sk-*` control variables with the VietSub Studio palette.

The core drops every effect when the visitor prefers reduced motion.

To update, download `tabs/tabs.js` from a newer commit, replace this file and update the commit above.
`README-tabs.md` is the upstream usage note for the element.
