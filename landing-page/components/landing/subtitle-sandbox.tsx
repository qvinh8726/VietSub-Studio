"use client";

import {
  ChevronDown,
  ChevronUp,
  CircleAlert,
  Coffee,
  CookingPot,
  Cpu,
  Crop,
  Headphones,
  Pause,
  Play,
  ScanText,
  SkipForward,
  TriangleAlert,
  Wand2,
  Check,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { KeyboardEvent, PointerEvent as ReactPointerEvent } from "react";
import { SANDBOX_LOOP_SECONDS, SANDBOX_PLATFORMS } from "./data";
import type { SandboxPlatform, SubtitleLine } from "./data";
import { PlatformTabs } from "./platform-tabs";
import { usePrefersReducedMotion } from "./use-reduced-motion";

/** Height of the yellow OCR box, as a fraction of the video height. */
const CROP_HEIGHT = 0.14;
const START_TIME = 1.6;
/** Characters per second for the translation typewriter. */
const TYPE_SPEED = 38;
const TYPE_DELAY = 0.25;

const ICONS = { headphones: Headphones, cpu: Cpu, coffee: Coffee, pot: CookingPot } as const;

type Read = "full" | "partial" | "empty" | "off";

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

function fitTop(platform: SandboxPlatform) {
  return clamp(platform.band.top - (CROP_HEIGHT - platform.band.height) / 2, 0, 1 - CROP_HEIGHT);
}

function coverageOf(top: number, platform: SandboxPlatform) {
  const { top: bandTop, height } = platform.band;
  const overlap = Math.min(top + CROP_HEIGHT, bandTop + height) - Math.max(top, bandTop);
  return clamp(overlap / height, 0, 1);
}

function readOf(cropOn: boolean, coverage: number): Read {
  if (!cropOn) return "off";
  if (coverage >= 0.7) return "full";
  if (coverage >= 0.25) return "partial";
  return "empty";
}

function srtTime(seconds: number) {
  const ms = Math.round(seconds * 1000);
  const h = Math.floor(ms / 3_600_000);
  const m = Math.floor((ms % 3_600_000) / 60_000);
  const s = Math.floor((ms % 60_000) / 1000);
  const pad = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${pad(h)}:${pad(m)}:${pad(s)},${pad(ms % 1000, 3)}`;
}

/** Cut a line to the share of it the box covers, the way OCR reads a half-covered line. */
function cut(text: string, share: number) {
  const keep = Math.max(1, Math.round(text.length * share * 0.8));
  return `${text.slice(0, keep)}…`;
}

function ocrText(line: SubtitleLine, read: Read, coverage: number, platform: SandboxPlatform) {
  if (read === "full") return line.zh;
  if (read === "partial") return cut(line.zh, coverage);
  if (read === "off") return `${line.zh} ${platform.noise}`;
  return "";
}

function viText(line: SubtitleLine, read: Read, coverage: number, platform: SandboxPlatform) {
  if (read === "full") return line.vi;
  if (read === "partial") return cut(line.vi, coverage);
  if (read === "off") return `${line.vi} (${platform.noise})`;
  return "";
}

const STATUS: Record<Read, { tone: "ok" | "warn" | "bad"; text: (coverage: number, platform: SandboxPlatform) => string }> = {
  full: { tone: "ok", text: (c) => `Khung phủ ${Math.round(c * 100)}% dòng chữ, OCR đọc đủ câu.` },
  partial: { tone: "warn", text: (c) => `Khung chỉ phủ ${Math.round(c * 100)}% dòng chữ nên câu bị cắt. Kéo khung xuống đúng vùng chữ.` },
  empty: { tone: "bad", text: () => "Khung không chứa dòng chữ nào. Bấm “Tự khớp vùng chữ”." },
  off: { tone: "warn", text: (_, p) => `Không cắt khung: OCR quét cả khung hình và lẫn cả “${p.noise}”.` },
};

export function SubtitleSandbox() {
  const reduce = usePrefersReducedMotion();
  const [platformIndex, setPlatformIndex] = useState(0);
  const platform = SANDBOX_PLATFORMS[platformIndex];
  const [cropOn, setCropOn] = useState(true);
  const [cropTop, setCropTop] = useState(() => fitTop(SANDBOX_PLATFORMS[0]));
  const [time, setTime] = useState(START_TIME);
  /** null means "follow the motion setting": play unless the visitor asked for reduced motion. */
  const [userPlaying, setUserPlaying] = useState<boolean | null>(null);
  const [inView, setInView] = useState(false);
  const [pageVisible, setPageVisible] = useState(true);
  const [dragging, setDragging] = useState(false);
  const [announcement, setAnnouncement] = useState("");

  const rootRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLDivElement>(null);
  const timeRef = useRef(START_TIME);
  const dragRef = useRef<{ startY: number; startTop: number; height: number; next: number } | null>(null);
  const dragFrame = useRef(0);

  const playing = userPlaying ?? !reduce;
  const running = playing && inView && pageVisible;

  // Pause when the sandbox leaves the screen or the tab is hidden.
  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const observer = new IntersectionObserver(([entry]) => setInView(entry.isIntersecting), { threshold: 0.15 });
    observer.observe(el);
    const onVisibility = () => setPageVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      observer.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  // The playback clock: rAF for timing, React state about 15 times a second.
  useEffect(() => {
    if (!running) return;
    let frame = 0;
    let last = performance.now();
    let lastCommit = last;
    const loop = (now: number) => {
      timeRef.current = (timeRef.current + Math.min(0.1, (now - last) / 1000)) % SANDBOX_LOOP_SECONDS;
      last = now;
      if (now - lastCommit > 66) {
        lastCommit = now;
        setTime(timeRef.current);
      }
      frame = requestAnimationFrame(loop);
    };
    frame = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(frame);
  }, [running]);

  useEffect(() => () => cancelAnimationFrame(dragFrame.current), []);

  const coverage = coverageOf(cropTop, platform);
  const read = readOf(cropOn, coverage);
  const lineIndex = platform.lines.findIndex((l) => time >= l.start && time < l.end);
  const line = lineIndex >= 0 ? platform.lines[lineIndex] : null;
  const status = STATUS[read];
  const statusText = status.text(coverage, platform);

  const typed = (text: string, l: SubtitleLine, current: boolean) => {
    if (!current || reduce) return text;
    const count = Math.floor((time - l.start - TYPE_DELAY) * TYPE_SPEED);
    return count <= 0 ? "" : text.slice(0, count);
  };

  // SRT blocks up to the current time in this loop.
  const shown = platform.lines.filter((l) => l.start <= time);

  const changePlatform = useCallback((index: number) => {
    setPlatformIndex(index);
    setAnnouncement(`Nguồn: ${SANDBOX_PLATFORMS[index].label}.`);
  }, []);

  const moveCrop = (next: number, say = true) => {
    const top = clamp(next, 0, 1 - CROP_HEIGHT);
    setCropTop(top);
    if (say) {
      const r = readOf(cropOn, coverageOf(top, platform));
      setAnnouncement(STATUS[r].text(coverageOf(top, platform), platform));
    }
  };

  const toggleCrop = () => {
    const next = !cropOn;
    setCropOn(next);
    const r = readOf(next, coverage);
    setAnnouncement(next ? `Bật khung OCR. ${STATUS[r].text(coverage, platform)}` : STATUS.off.text(coverage, platform));
  };

  const togglePlay = () => {
    setUserPlaying(!playing);
    setAnnouncement(playing ? "Đã tạm dừng mô phỏng." : "Đang phát mô phỏng.");
  };

  const nextLine = () => {
    const after = platform.lines.find((l) => l.start > time + 0.01) ?? platform.lines[0];
    const target = Math.min(after.end - 0.05, after.start + TYPE_DELAY + after.vi.length / TYPE_SPEED + 0.05);
    timeRef.current = target;
    setTime(target);
    const r = readOf(cropOn, coverage);
    const text = viText(after, r, coverage, platform);
    setAnnouncement(text ? `Câu ${platform.lines.indexOf(after) + 1}: ${text}` : statusText);
  };

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || !videoRef.current) return;
    // Stop the browser from starting a text selection; keep focus on the box for the arrow keys.
    event.preventDefault();
    event.currentTarget.focus({ preventScroll: true });
    event.currentTarget.setPointerCapture(event.pointerId);
    const height = videoRef.current.getBoundingClientRect().height;
    dragRef.current = { startY: event.clientY, startTop: cropTop, height, next: cropTop };
    setDragging(true);
  };

  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.height <= 0) return;
    drag.next = clamp(drag.startTop + (event.clientY - drag.startY) / drag.height, 0, 1 - CROP_HEIGHT);
    if (dragFrame.current) return;
    dragFrame.current = requestAnimationFrame(() => {
      dragFrame.current = 0;
      if (dragRef.current) setCropTop(dragRef.current.next);
    });
  };

  const endDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    dragRef.current = null;
    setDragging(false);
    moveCrop(drag.next);
  };

  const onCropKey = (event: KeyboardEvent<HTMLDivElement>) => {
    const steps: Record<string, number> = { ArrowUp: -0.02, ArrowDown: 0.02, PageUp: -0.1, PageDown: 0.1 };
    if (event.key in steps) {
      event.preventDefault();
      moveCrop(cropTop + steps[event.key]);
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      moveCrop(event.key === "Home" ? 0 : 1);
    }
  };

  const Icon = ICONS[platform.icon];
  const vertical = platform.ratio < 1;
  const progress = (time / SANDBOX_LOOP_SECONDS) * 100;
  const subtitleSize = `${platform.band.height * 34}cqh`;

  return (
    <div ref={rootRef} className="sandbox" aria-label="Mô phỏng VideOCR và dịch phụ đề" role="region">
      <div className="sandbox-bar">
        <PlatformTabs tabs={SANDBOX_PLATFORMS} index={platformIndex} onChange={changePlatform} label="Nguồn video" />
        <span className="sim-badge">
          <span aria-hidden="true" className={`sim-dot ${running ? "is-live" : ""}`} />
          Mô phỏng
        </span>
      </div>

      <div className="sandbox-grid" role="tabpanel" aria-label={`Video mẫu ${platform.label}`}>
        <div className="sandbox-stage-col">
          <div className="stage" style={{ backgroundImage: `${platform.glow}, ${platform.scene}` }}>
            <div
              ref={videoRef}
              className="stage-video"
              style={{
                aspectRatio: String(platform.ratio),
                ...(vertical ? { height: "100%" } : { width: "100%" }),
                backgroundImage: `${platform.glow}, ${platform.scene}`,
              }}
            >
              <div className="stage-share" lang="zh-CN" aria-hidden="true">
                <span>{platform.han}</span>
              </div>
              <Icon className="stage-icon" aria-hidden="true" strokeWidth={1.1} />
              <span className="stage-watermark" lang="zh-CN" aria-hidden="true">
                {platform.watermark}
              </span>
              {platform.id === "bilibili" ? (
                <span className="stage-danmaku" lang="zh-CN" aria-hidden="true">
                  {platform.noise}
                </span>
              ) : null}
              {line ? (
                <p
                  className="hardsub"
                  lang="zh-CN"
                  aria-hidden="true"
                  style={{ top: `${platform.band.top * 100}%`, height: `${platform.band.height * 100}%`, fontSize: subtitleSize }}
                >
                  {line.zh}
                </p>
              ) : null}

              {cropOn ? (
                <div
                  className={`crop-box ${dragging ? "is-dragging" : ""} is-${read}`}
                  style={{ top: `${cropTop * 100}%`, height: `${CROP_HEIGHT * 100}%` }}
                  role="slider"
                  tabIndex={0}
                  aria-label="Vị trí khung OCR"
                  aria-orientation="vertical"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={Math.round(cropTop * 100)}
                  aria-valuetext={`Cách mép trên ${Math.round(cropTop * 100)}% khung hình, phủ ${Math.round(coverage * 100)}% dòng chữ`}
                  onPointerDown={onPointerDown}
                  onPointerMove={onPointerMove}
                  onPointerUp={endDrag}
                  onPointerCancel={endDrag}
                  onKeyDown={onCropKey}
                >
                  <span className="crop-label">
                    <Crop size={12} aria-hidden="true" /> Vùng OCR · {Math.round(coverage * 100)}%
                  </span>
                  <span className="crop-grip" aria-hidden="true" />
                </div>
              ) : (
                <div className="crop-off" aria-hidden="true">
                  <span>Quét cả khung hình</span>
                </div>
              )}
            </div>
            <div className="stage-progress" aria-hidden="true">
              <span style={{ width: `${progress}%` }} />
            </div>
            <span className="stage-time" aria-hidden="true">
              {srtTime(time).slice(3, 9).replace(",", ".")} / 00:10
            </span>
          </div>

          <div className="sandbox-controls" role="group" aria-label="Điều khiển mô phỏng">
            <button type="button" className="ctl" onClick={togglePlay} aria-pressed={!playing}>
              {playing ? <Pause size={16} aria-hidden="true" /> : <Play size={16} aria-hidden="true" />}
              {playing ? "Tạm dừng" : "Phát"}
            </button>
            <button type="button" className="ctl" onClick={nextLine}>
              <SkipForward size={16} aria-hidden="true" /> Câu tiếp
            </button>
            <span className="ctl-sep" aria-hidden="true" />
            <button type="button" className="ctl ctl-icon" onClick={() => moveCrop(cropTop - 0.04)} disabled={!cropOn} aria-label="Dời khung OCR lên">
              <ChevronUp size={18} aria-hidden="true" />
            </button>
            <button type="button" className="ctl ctl-icon" onClick={() => moveCrop(cropTop + 0.04)} disabled={!cropOn} aria-label="Dời khung OCR xuống">
              <ChevronDown size={18} aria-hidden="true" />
            </button>
            <button type="button" className="ctl" onClick={() => moveCrop(fitTop(platform))} disabled={!cropOn}>
              <Wand2 size={16} aria-hidden="true" /> Tự khớp vùng chữ
            </button>
            <button type="button" role="switch" aria-checked={cropOn} className="ctl switch" onClick={toggleCrop}>
              <span className="switch-track" aria-hidden="true">
                <span />
              </span>
              Khung OCR
            </button>
          </div>
        </div>

        <div className="sandbox-output">
          <div className={`ocr-status tone-${status.tone}`}>
            {status.tone === "ok" ? (
              <Check size={16} aria-hidden="true" />
            ) : status.tone === "warn" ? (
              <TriangleAlert size={16} aria-hidden="true" />
            ) : (
              <CircleAlert size={16} aria-hidden="true" />
            )}
            <span>{statusText}</span>
          </div>

          <div className="out-card">
            <div className="out-head">
              <ScanText size={15} aria-hidden="true" />
              <span>VideOCR</span>
              <code>{platform.id}.raw.srt</code>
            </div>
            <p className="out-zh" lang="zh-CN">
              {line ? ocrText(line, read, coverage, platform) || "—" : <span className="out-wait">Chờ câu tiếp theo…</span>}
            </p>
          </div>

          <div className="out-card out-card-vi">
            <div className="out-head">
              <span className="out-lang">VI</span>
              <span>DeepSeek API</span>
              <code>{platform.id}.vi.srt</code>
            </div>
            <pre className="srt" aria-label="Nội dung file .vi.srt">
              {shown.length === 0 ? (
                <span className="out-wait">Chưa có câu nào trong vòng phát này.</span>
              ) : (
                shown.map((l, i) => {
                  const current = l === line;
                  const text = typed(viText(l, read, coverage, platform), l, current);
                  return (
                    <span key={`${platform.id}-${i}`} className={`srt-block ${current ? "is-current" : ""}`}>
                      <span className="srt-index">{platform.lines.indexOf(l) + 1}</span>
                      {"\n"}
                      <span className="srt-time">
                        {srtTime(l.start)} --&gt; {srtTime(l.end)}
                      </span>
                      {"\n"}
                      <span className="srt-text">
                        {text || (read === "empty" ? "(trống)" : "")}
                        {current && !reduce && text.length < viText(l, read, coverage, platform).length ? (
                          <span className="caret" aria-hidden="true" />
                        ) : null}
                      </span>
                      {"\n\n"}
                    </span>
                  );
                })
              )}
            </pre>
          </div>
        </div>
      </div>

      <p className="sr-only" aria-live="polite">
        {announcement}
      </p>
    </div>
  );
}
