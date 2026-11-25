# VietSub Studio v2.1.0 — Soft Cinema landing

## Design & scope

Production Next.js + TypeScript landing page with a dark cinema foundation, warm gold / sage-mint / coral accents, soft ambient aurora, large rounded surfaces, subtle grain and restrained frosted-glass depth. Be Vietnam Pro, Noto Sans SC and JetBrains Mono are self-hosted by Next's font build.

Only files inside `landing-page/` were changed. The desktop application was not modified. No git staging, commit or push was performed.

## Product demo

The Hero's three workspaces are **local simulations**. They never download a source, invoke AI, generate speech, render video or connect to/publish to an account.

- VietSub: four source samples; move the OCR region by pointer, keyboard arrows, nudge buttons or fit-to-text; preview SRT changes; play/pause/seek the sample clock.
- Auto Edit: Chinese-text masking, subtitle burn-in, TTS track illustration, combined speed/pitch switch and independent rate/pitch controls.
- Publish: independent mock account cards, multi-select, local datetime and clearly labelled demo scheduling feedback.
- Hidden panels remain mounted to preserve state. The sample clock pauses when a panel is hidden, offscreen or the document is not visible.

Real-product limits are stated in the copy: conditional HTTP Range ×6; manual login/CAPTCHA; optional external separation models; unofficial CapCut service; CPU render fallback; app/machine-running requirement for local schedules; platform-specific uploader capabilities; **CapCut Draft package export is experimental in v2.1.0 and is not guaranteed to work with every CapCut version**.

## Run

```powershell
npm install
npm run lint
npm run typecheck
npm run build
npm start -- --hostname 127.0.0.1 --port 3187
```

In another terminal, with Chrome installed:

```powershell
npm run qa:browser
npm run qa:a11y
npm run qa:lifecycle
```

`LANDING_QA_URL` overrides the default `http://127.0.0.1:3187`. Browser tests write their images and machine-readable results only under `artifacts/`.

## Motion and accessibility

- `motion/react` spring reveals, shared-layout tab pill and tactile CTA feedback.
- OS reduced-motion disables ambient loops, marquee, parallax, reveals and VFX; the marquee exposes a manual pause button for normal motion.
- Sakuga finite effects stop their clock when idle, cancel on hidden/reduced-motion and release canvas/listeners on unmount. Local lifecycle adapters are documented beside the vendored script.
- Native buttons, switches, labelled inputs, roving keyboard tabs, visible focus, skip link and drag alternatives.

## Image credit

The local landscape illustration is an optimized WebP derived from [this Unsplash photograph](https://images.unsplash.com/photo-1501785888041-af3ef285b470). It is used as a sample creative scene, **not** a recording from a supported source platform or an actual app output. No external photo request is made at runtime.

## Validation artifacts

QA scripts generate screenshots and JSON results in the local, gitignored `artifacts/` directory. `QA_REPORT.md` is a temporary local report and is also ignored; it is not release documentation. Run the scripts above to regenerate evidence for the current build.
