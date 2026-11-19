"use client";

import { LayoutGroup, MotionConfig, motion } from "motion/react";
import {
  ArrowRight,
  AudioLines,
  CalendarClock,
  Check,
  ChevronDown,
  ChevronUp,
  CircleCheck,
  Clapperboard,
  FileText,
  Layers2,
  Move,
  Pause,
  Play,
  ScanLine,
  Send,
  ShieldCheck,
  Sparkles,
  Subtitles,
} from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import type {
  CSSProperties,
  FormEvent,
  KeyboardEvent,
  PointerEvent as ReactPointerEvent,
  RefObject,
} from "react";
import { usePrefersReducedMotion } from "./use-reduced-motion";
import "./studio-showcase.css";

const TABS = [
  { label: "VietSub", icon: Subtitles },
  { label: "Auto Edit", icon: Clapperboard },
  { label: "Publish", icon: Send },
] as const;

const SOURCES = [
  {
    id: "douyin",
    label: "Douyin",
    han: "抖",
    band: 0.76,
    zh: [
      "山水之间，慢下来生活。",
      "小船载着我们，驶向远方。",
      "把平凡的日子，过成喜欢的模样。",
    ],
    vi: [
      "Giữa non nước, mình sống chậm lại.",
      "Con thuyền nhỏ đưa ta về phía bình yên.",
      "Biến ngày bình thường thành điều mình yêu.",
    ],
  },
  {
    id: "bilibili",
    label: "Bilibili",
    han: "哔",
    band: 0.72,
    zh: [
      "今天，带你看看山水间的风景。",
      "沿着河流，遇见不一样的世界。",
      "这段旅程，值得慢慢收藏。",
    ],
    vi: [
      "Hôm nay, cùng ngắm một miền non nước.",
      "Theo dòng sông, gặp một thế giới khác.",
      "Chuyến đi này, đáng để chậm rãi lưu lại.",
    ],
  },
  {
    id: "xhs",
    label: "XHS",
    han: "红",
    band: 0.78,
    zh: [
      "发现一个让人心动的地方。",
      "风吹过，连时间都慢了下来。",
      "下次，还想和你一起来。",
    ],
    vi: [
      "Tìm thấy một nơi khiến lòng rung động.",
      "Gió khẽ qua, thời gian cũng chậm lại.",
      "Lần tới, vẫn muốn cùng bạn đến đây.",
    ],
  },
  {
    id: "kuaishou",
    label: "Kuaishou",
    han: "快",
    band: 0.74,
    zh: [
      "家乡的山水，怎么看都不够。",
      "一条小河，装着小时候的回忆。",
      "走得再远，也记得回家的路。",
    ],
    vi: [
      "Non nước quê nhà, ngắm mãi chẳng chán.",
      "Dòng sông nhỏ cất giữ ký ức tuổi thơ.",
      "Đi xa đến mấy, vẫn nhớ lối về nhà.",
    ],
  },
] as const;

type CropBox = { x: number; y: number; width: number; height: number };
type DragState = {
  pointerId: number;
  clientX: number;
  clientY: number;
  box: CropBox;
  width: number;
  height: number;
};
type PanelProps = { enabled: boolean; reduced: boolean; id: string };

const INITIAL_CROP: CropBox = { x: 0.07, y: 0.665, width: 0.86, height: 0.24 };
const DURATION = 12;
const WAVEFORM = Array.from(
  { length: 48 },
  (_, i) => 0.18 + Math.abs(Math.sin(i * 1.81) * Math.cos(i * 0.37)) * 0.82,
);
const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value));
const timestamp = (seconds: number) =>
  `00:${String(Math.floor(seconds)).padStart(2, "0")}`;

function useStudioVisible(ref: RefObject<HTMLDivElement | null>) {
  const [inView, setInView] = useState(false);
  const [pageVisible, setPageVisible] = useState(true);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new IntersectionObserver(
      ([entry]) => setInView(entry.isIntersecting),
      { threshold: 0.1 },
    );
    observer.observe(element);
    const onVisibility = () =>
      setPageVisible(document.visibilityState === "visible");
    onVisibility();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      observer.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [ref]);

  return inView && pageVisible;
}

/** A deliberately local clock, not an external video, render, or TTS request. */
function useDemoPlayback(enabled: boolean) {
  const [time, setTime] = useState(2.8);
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    if (!enabled || !playing) return;
    const interval = window.setInterval(
      () => setTime((previous) => (previous + 0.1) % DURATION),
      100,
    );
    return () => window.clearInterval(interval);
  }, [enabled, playing]);

  return { time, setTime, playing, setPlaying };
}

function DemoTransport({
  clock,
  id,
}: {
  clock: ReturnType<typeof useDemoPlayback>;
  id: string;
}) {
  return (
    <div className="studio-transport">
      <button
        className="studio-play"
        type="button"
        aria-label={
          clock.playing ? "Tạm dừng preview mô phỏng" : "Phát preview mô phỏng"
        }
        aria-pressed={clock.playing}
        onClick={() => clock.setPlaying(!clock.playing)}
      >
        {clock.playing ? (
          <Pause size={17} fill="currentColor" aria-hidden="true" />
        ) : (
          <Play size={17} fill="currentColor" aria-hidden="true" />
        )}
      </button>
      <label className="studio-sr-only" htmlFor={`${id}-seek`}>
        Vị trí phát trong preview mô phỏng
      </label>
      <input
        id={`${id}-seek`}
        className="studio-seek"
        type="range"
        min="0"
        max={DURATION - 0.1}
        step="0.1"
        value={clock.time}
        aria-valuetext={`${clock.time.toFixed(1)} trên ${DURATION} giây`}
        onChange={(event) => clock.setTime(Number(event.target.value))}
      />
      <span className="studio-time">
        {timestamp(clock.time)} <span>/ 00:12</span>
      </span>
    </div>
  );
}

function SceneBadges({ label, source }: { label: string; source?: string }) {
  return (
    <div className="studio-scene-top" aria-hidden="true">
      <span className="studio-scene-label">
        <span className="studio-live-dot" />
        {label}
      </span>
      <span className="studio-scene-resolution">{source || "1920 × 1080"}</span>
    </div>
  );
}

function cropCoverage(box: CropBox, band: number) {
  const vertical = clamp(
    (Math.min(box.y + box.height, band + 0.075) - Math.max(box.y, band)) /
      0.075,
    0,
    1,
  );
  const horizontal = clamp(
    (Math.min(box.x + box.width, 0.87) - Math.max(box.x, 0.13)) / 0.74,
    0,
    1,
  );
  return vertical * horizontal;
}

function VietSubPanel({ enabled, id }: PanelProps) {
  const [sourceIndex, setSourceIndex] = useState(0);
  const [crop, setCrop] = useState<CropBox>(INITIAL_CROP);
  const [dragging, setDragging] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  const previewRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<DragState | null>(null);
  const clock = useDemoPlayback(enabled);
  const source = SOURCES[sourceIndex];
  const cue = Math.min(2, Math.floor(clock.time / 4));
  const coverage = cropCoverage(crop, source.band);
  const complete = coverage >= 0.72;
  const partial = coverage >= 0.2;
  const sample = complete
    ? source.vi[cue]
    : partial
      ? `${source.vi[cue].slice(0, Math.max(5, Math.floor(source.vi[cue].length * coverage)))}…`
      : "[Chưa bắt trúng vùng chữ]";
  const status = complete
    ? "Đã bắt trọn dòng chữ"
    : partial
      ? "Vùng chữ đang bị cắt"
      : "Kéo khung về vùng phụ đề";

  const updateCrop = (x: number, y: number) => {
    setCrop((previous) => ({
      ...previous,
      x: clamp(x, 0, 1 - previous.width),
      y: clamp(y, 0, 1 - previous.height),
    }));
  };

  const fitCrop = () => {
    setCrop({
      ...INITIAL_CROP,
      y: clamp(source.band - 0.085, 0, 1 - INITIAL_CROP.height),
    });
    setAnnouncement("Đã đưa vùng OCR về đúng dòng phụ đề mẫu.");
  };

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || !previewRef.current || !event.isPrimary) return;
    event.preventDefault();
    event.currentTarget.focus({ preventScroll: true });
    event.currentTarget.setPointerCapture(event.pointerId);
    const bounds = previewRef.current.getBoundingClientRect();
    dragRef.current = {
      pointerId: event.pointerId,
      clientX: event.clientX,
      clientY: event.clientY,
      box: crop,
      width: bounds.width,
      height: bounds.height,
    };
    setDragging(true);
  };

  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (
      !drag ||
      drag.pointerId !== event.pointerId ||
      !drag.width ||
      !drag.height
    )
      return;
    updateCrop(
      drag.box.x + (event.clientX - drag.clientX) / drag.width,
      drag.box.y + (event.clientY - drag.clientY) / drag.height,
    );
  };

  const finishDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    dragRef.current = null;
    setDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
    setAnnouncement(`${status}. Mẫu phụ đề đã cập nhật.`);
  };

  const onCropKey = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.shiftKey ? 0.04 : 0.01;
    if (
      ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home"].includes(
        event.key,
      )
    )
      event.preventDefault();
    if (event.key === "ArrowUp") updateCrop(crop.x, crop.y - step);
    if (event.key === "ArrowDown") updateCrop(crop.x, crop.y + step);
    if (event.key === "ArrowLeft") updateCrop(crop.x - step, crop.y);
    if (event.key === "ArrowRight") updateCrop(crop.x + step, crop.y);
    if (event.key === "Home") fitCrop();
  };

  const boxStyle: CSSProperties = {
    left: `${crop.x * 100}%`,
    top: `${crop.y * 100}%`,
    width: `${crop.width * 100}%`,
    height: `${crop.height * 100}%`,
  };

  return (
    <div className="studio-stage-grid">
      <div className="studio-media-column">
        <div className="studio-scene studio-ocr-scene" ref={previewRef}>
          <SceneBadges label="PREVIEW MÔ PHỎNG" source={source.label} />
          <div className="studio-photo-credit" aria-hidden="true">
            <span>MỘT CHÚT BÌNH YÊN</span>
            <span>Phong cảnh minh họa</span>
          </div>
          <div
            className="studio-chinese"
            style={{ top: `${(source.band + 0.0375) * 100}%` }}
            lang="zh-Hans"
          >
            {source.zh[cue]}
          </div>
          <div
            className={`studio-crop ${dragging ? "studio-crop-dragging" : ""}`}
            style={boxStyle}
            role="group"
            aria-roledescription="vùng chọn OCR"
            tabIndex={0}
            aria-label="Vùng OCR. Dùng các phím mũi tên để di chuyển, Shift để đi nhanh, Home để khớp vùng chữ."
            aria-describedby={`${id}-crop-hint`}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={finishDrag}
            onPointerCancel={finishDrag}
            onLostPointerCapture={() => {
              dragRef.current = null;
              setDragging(false);
            }}
            onKeyDown={onCropKey}
          >
            <span className="studio-crop-label">
              <ScanLine size={12} aria-hidden="true" /> VÙNG OCR{" "}
              <Move size={11} aria-hidden="true" />
            </span>
            <span className="studio-crop-corner studio-crop-tl" />
            <span className="studio-crop-corner studio-crop-tr" />
            <span className="studio-crop-corner studio-crop-bl" />
            <span className="studio-crop-corner studio-crop-br" />
          </div>
        </div>
        <DemoTransport clock={clock} id={id} />
        <div className="studio-media-foot">
          <span>
            <Move size={13} aria-hidden="true" /> Kéo khung vàng để thử OCR
          </span>
          <span>Ảnh & phụ đề mẫu</span>
        </div>
      </div>
      <div className="studio-controls">
        <div className="studio-step-label">
          <span>01</span> TỪ VIDEO ĐẾN TIẾNG VIỆT
        </div>
        <h3>
          Giữ khung hình.
          <br />
          <span>Mở câu chuyện.</span>
        </h3>
        <p className="studio-panel-intro">
          Chọn nguồn. Khoanh đúng dòng chữ. Phần còn lại, để Studio tiếp nối.
        </p>
        <fieldset className="studio-fieldset">
          <legend className="studio-field-label">Nguồn video mẫu</legend>
          <div className="studio-sources">
            {SOURCES.map((item, index) => (
              <button
                type="button"
                key={item.id}
                className={`studio-source ${index === sourceIndex ? "studio-source-selected" : ""}`}
                aria-pressed={index === sourceIndex}
                onClick={() => {
                  setSourceIndex(index);
                  setAnnouncement(`Đã chọn nguồn mẫu ${item.label}.`);
                }}
              >
                <span
                  className="studio-source-han"
                  lang="zh-Hans"
                  aria-hidden="true"
                >
                  {item.han}
                </span>
                <span>{item.label}</span>
              </button>
            ))}
          </div>
        </fieldset>
        <div className="studio-crop-tools">
          <div>
            <span
              className={`studio-crop-status ${complete ? "studio-status-good" : "studio-status-warn"}`}
            >
              {complete ? (
                <CircleCheck size={14} aria-hidden="true" />
              ) : (
                <ScanLine size={14} aria-hidden="true" />
              )}
              {status}
            </span>
            <span className="studio-crop-coords">
              X {Math.round(crop.x * 100)}% · Y {Math.round(crop.y * 100)}%
            </span>
          </div>
          <div className="studio-nudge-group">
            <button
              className="studio-icon-button"
              type="button"
              aria-label="Di chuyển vùng OCR lên"
              onClick={() => updateCrop(crop.x, crop.y - 0.04)}
            >
              <ChevronUp size={17} aria-hidden="true" />
            </button>
            <button
              className="studio-icon-button"
              type="button"
              aria-label="Di chuyển vùng OCR xuống"
              onClick={() => updateCrop(crop.x, crop.y + 0.04)}
            >
              <ChevronDown size={17} aria-hidden="true" />
            </button>
            <button
              className="studio-fit-button"
              type="button"
              onClick={fitCrop}
            >
              <ScanLine size={14} aria-hidden="true" />
              Khớp chữ
            </button>
          </div>
        </div>
        <p id={`${id}-crop-hint`} className="studio-key-hint">
          Có thể dùng phím mũi tên trên khung · Shift: nhanh · Home: khớp chữ.
        </p>
        <div className="studio-srt">
          <div className="studio-srt-head">
            <span>
              <FileText size={13} aria-hidden="true" /> song-nui.vi.srt
            </span>
            <span className="studio-sample-tag">MẪU</span>
          </div>
          <div className="studio-srt-body">
            <span className="studio-srt-index">{cue + 1}</span>
            <span className="studio-srt-time">
              00:00:{String(cue * 4).padStart(2, "0")},000 → 00:00:
              {String(cue * 4 + 4).padStart(2, "0")},000
            </span>
            <p>{sample}</p>
          </div>
        </div>
        <p className="studio-truth-note">
          HTTP Range ×6 chỉ khi máy chủ hỗ trợ. Gặp Captcha, app hiện trình
          duyệt để bạn xác minh.
        </p>
        <span className="studio-sr-only" role="status" aria-live="polite">
          {announcement}
        </span>
      </div>
    </div>
  );
}

function StudioSwitch({
  checked,
  onChange,
  icon: Icon,
  title,
  description,
}: {
  checked: boolean;
  onChange: () => void;
  icon: typeof Subtitles;
  title: string;
  description: string;
}) {
  return (
    <button
      className="studio-switch-row"
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={title}
      onClick={onChange}
    >
      <span className="studio-switch-icon">
        <Icon size={17} aria-hidden="true" />
      </span>
      <span className="studio-switch-copy">
        <strong>{title}</strong>
        <span>{description}</span>
      </span>
      <span
        className={`studio-switch-track ${checked ? "studio-switch-on" : ""}`}
        aria-hidden="true"
      >
        <span>{checked && <Check size={10} />}</span>
      </span>
    </button>
  );
}

function AutoEditPanel({ enabled, reduced, id }: PanelProps) {
  const [blur, setBlur] = useState(true);
  const [burn, setBurn] = useState(true);
  const [tts, setTts] = useState(true);
  const [speed, setSpeed] = useState(1);
  const [pitch, setPitch] = useState(0);
  const clock = useDemoPlayback(enabled);
  const cue = Math.min(2, Math.floor(clock.time / 4));

  return (
    <div className="studio-stage-grid">
      <div className="studio-media-column">
        <div className="studio-scene studio-edit-scene">
          <SceneBadges label="BẢN DỰNG MÔ PHỎNG" />
          <div className="studio-photo-credit" aria-hidden="true">
            <span>MỘT CHÚT BÌNH YÊN</span>
            <span>Giữ lại điều mình yêu.</span>
          </div>
          <div
            className={`studio-edit-chinese ${blur ? "studio-edit-blurred" : ""}`}
            lang="zh-Hans"
          >
            {SOURCES[0].zh[cue]}
          </div>
          {burn && (
            <div className="studio-burned-sub">{SOURCES[0].vi[cue]}</div>
          )}
          <span className="studio-edit-badge">
            <Layers2 size={12} aria-hidden="true" />{" "}
            {blur ? "Che chữ gốc" : "Giữ chữ gốc"}
            {burn ? " + Sub Việt" : ""}
          </span>
        </div>
        <DemoTransport clock={clock} id={id} />
        <div
          className={`studio-waveform-card ${tts ? "studio-waveform-active" : ""}`}
        >
          <div className="studio-waveform-head">
            <span>
              <AudioLines size={14} aria-hidden="true" />
              {tts ? "CapCut TTS" : "TTS đang tắt"}
            </span>
            <strong>
              {speed.toFixed(2)}×{" "}
              <span>
                · {pitch > 0 ? "+" : ""}
                {pitch} st
              </span>
            </strong>
          </div>
          <div className="studio-waveform" aria-hidden="true">
            {WAVEFORM.map((bar, index) => (
              <span
                key={index}
                style={{
                  transform: `scaleY(${tts ? Math.min(1, bar * speed * (1 + pitch * 0.07) + Math.sin(index * speed) * 0.08) : 0.09})`,
                  transitionDuration: reduced ? "0s" : "240ms",
                }}
              />
            ))}
          </div>
          <span className="studio-waveform-note">
            Sóng âm minh họa · Không phát âm thanh
          </span>
        </div>
      </div>
      <div className="studio-controls">
        <div className="studio-step-label">
          <span>02</span> THÊM CHẤT RIÊNG CHO VIDEO
        </div>
        <h3>
          Một chút chỉnh sửa.
          <br />
          <span>Một bản sắc mới.</span>
        </h3>
        <p className="studio-panel-intro">
          Che chữ gốc, gắn sub Việt và thử nhịp đọc phù hợp với câu chuyện.
        </p>
        <div className="studio-switches">
          <StudioSwitch
            checked={blur}
            onChange={() => setBlur(!blur)}
            icon={ScanLine}
            title="Che chữ Trung"
            description="Mô phỏng vùng boxblur"
          />
          <StudioSwitch
            checked={burn}
            onChange={() => setBurn(!burn)}
            icon={Subtitles}
            title="Burn sub Việt"
            description="Phụ đề gắn vào khung hình"
          />
          <StudioSwitch
            checked={tts}
            onChange={() => setTts(!tts)}
            icon={AudioLines}
            title="CapCut TTS"
            description="Track đọc theo từng câu SRT"
          />
          <StudioSwitch
            checked={speed === 1.15 && pitch === 2}
            onChange={() => {
              const boosted = speed === 1.15 && pitch === 2;
              setSpeed(boosted ? 1 : 1.15);
              setPitch(boosted ? 0 : 2);
            }}
            icon={Sparkles}
            title="Tốc độ & cao độ 1-click"
            description="Thử nhịp 1.15× và giọng sáng +2 st"
          />
        </div>
        <div className="studio-voice-controls">
          <fieldset className="studio-fieldset">
            <legend className="studio-field-label">Tốc độ đọc</legend>
            <div className="studio-segments">
              {[0.9, 1, 1.15].map((rate) => (
                <button
                  type="button"
                  key={rate}
                  aria-pressed={speed === rate}
                  className={speed === rate ? "studio-segment-selected" : ""}
                  onClick={() => setSpeed(rate)}
                >
                  {rate === 1 ? "1.0" : rate}×
                </button>
              ))}
            </div>
          </fieldset>
          <fieldset className="studio-fieldset">
            <legend className="studio-field-label">Cao độ</legend>
            <div className="studio-segments">
              {[
                { value: -2, label: "Trầm" },
                { value: 0, label: "Gốc" },
                { value: 2, label: "Sáng" },
              ].map((option) => (
                <button
                  type="button"
                  key={option.value}
                  aria-label={`${option.label}, ${option.value > 0 ? "+" : ""}${option.value} semitone`}
                  aria-pressed={pitch === option.value}
                  className={
                    pitch === option.value ? "studio-segment-selected" : ""
                  }
                  onClick={() => setPitch(option.value)}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </fieldset>
        </div>
        <p className="studio-truth-note">
          CapCut là dịch vụ không chính thức, tùy khả dụng. Tách giọng cần
          model/runtime ngoài, không bắt buộc. Có thể xuất gói CapCut Draft thử
          nghiệm; không bảo đảm tương thích mọi phiên bản CapCut.
        </p>
      </div>
    </div>
  );
}

const PROFILES = [
  {
    id: "tiktok",
    name: "TikTok",
    handle: "@cham.vietnam",
    mark: "Tk",
    tone: "mint",
  },
  {
    id: "youtube",
    name: "YouTube Shorts",
    handle: "Chạm Việt Nam",
    mark: "Yt",
    tone: "verm",
  },
  {
    id: "facebook",
    name: "Facebook Reels",
    handle: "Chạm · Facebook Page",
    mark: "Fb",
    tone: "blue",
  },
  {
    id: "bilibili",
    name: "Bilibili",
    handle: "慢游日记",
    mark: "哔",
    tone: "blue",
  },
  {
    id: "douyin",
    name: "Douyin",
    handle: "慢游越南",
    mark: "抖",
    tone: "mint",
  },
  { id: "xhs", name: "XHS", handle: "山水慢生活", mark: "红", tone: "verm" },
] as const;

function PublishPanel({ id }: PanelProps) {
  const [selected, setSelected] = useState<string[]>(["tiktok", "youtube"]);
  const [schedule, setSchedule] = useState("");
  const [feedback, setFeedback] = useState("");
  const selectedProfiles = PROFILES.filter((profile) =>
    selected.includes(profile.id),
  );
  const previewProfiles = [
    ...selectedProfiles,
    ...PROFILES.filter((profile) => !selected.includes(profile.id)),
  ].slice(0, 3);

  const toggleProfile = (profileId: string) => {
    setSelected((previous) =>
      previous.includes(profileId)
        ? previous.filter((item) => item !== profileId)
        : [...previous, profileId],
    );
    setFeedback("");
  };

  const onSchedule = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!schedule || !selectedProfiles.length) return;
    const displayTime = new Date(schedule).toLocaleString("vi-VN", {
      hour: "2-digit",
      minute: "2-digit",
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
    });
    setFeedback(
      `Đã tạo lịch DEMO cho ${selectedProfiles.length} hồ sơ lúc ${displayTime} (giờ thiết bị). Không video nào được gửi hay đăng.`,
    );
  };

  return (
    <div className="studio-stage-grid">
      <div className="studio-media-column studio-publish-media">
        <div className="studio-publish-art">
          <div className="studio-publish-art-top">
            <span className="studio-editorial-kicker">TỪ MỘT CÂU CHUYỆN</span>
            <span className="studio-draft-badge">
              <span />
              Bản nháp mẫu
            </span>
          </div>
          <h4>
            Một chuyến đi.
            <br />
            <span>Nhiều điểm chạm.</span>
          </h4>
          <div
            className="studio-posters"
            aria-label="Các bản xem trước minh họa theo kênh đã chọn"
          >
            {previewProfiles.map((profile, index) => (
              <div
                key={profile.id}
                className={`studio-poster studio-poster-${index + 1}`}
              >
                <div className="studio-poster-picture">
                  <span className="studio-poster-number">0{index + 1}</span>
                  <span className="studio-poster-title">
                    đi chậm
                    <br />
                    <em>một chút.</em>
                  </span>
                  <span className="studio-poster-bottom">
                    PHONG CẢNH / MINH HỌA
                  </span>
                </div>
                <span className="studio-poster-platform">
                  {selected.includes(profile.id) && (
                    <Check size={12} aria-hidden="true" />
                  )}
                  {profile.name}
                </span>
              </div>
            ))}
          </div>
          <div className="studio-publish-caption">
            <FileText size={15} aria-hidden="true" />
            <span>
              song-nui.edited.mp4
              <strong>Một chút bình yên giữa non nước.</strong>
            </span>
            <span className="studio-publish-caption-tag">DEMO</span>
          </div>
        </div>
        <div className="studio-media-foot">
          <span>
            <ShieldCheck size={13} aria-hidden="true" /> Hồ sơ độc lập theo tài
            khoản
          </span>
          <span>Không đăng thật</span>
        </div>
      </div>
      <div className="studio-controls studio-publish-controls">
        <div className="studio-step-label">
          <span>03</span> ĐƯA CÂU CHUYỆN ĐI XA HƠN
        </div>
        <h3>
          Cùng nội dung.
          <br />
          <span>Đúng nơi bạn muốn.</span>
        </h3>
        <p className="studio-panel-intro">
          Chọn nhiều hồ sơ, thử lịch đăng cục bộ. Bạn luôn là người quyết định.
        </p>
        <fieldset className="studio-fieldset">
          <legend className="studio-field-label">
            Hồ sơ minh họa <span>· {selected.length} đã chọn</span>
          </legend>
          <div className="studio-profiles">
            {PROFILES.map((profile) => {
              const checked = selected.includes(profile.id);
              return (
                <button
                  type="button"
                  key={profile.id}
                  className={`studio-profile ${checked ? "studio-profile-selected" : ""}`}
                  aria-pressed={checked}
                  title="Hồ sơ demo độc lập, không kết nối tài khoản thật"
                  onClick={() => toggleProfile(profile.id)}
                >
                  <span
                    className={`studio-profile-avatar studio-tone-${profile.tone}`}
                    aria-hidden="true"
                  >
                    {profile.mark}
                  </span>
                  <span className="studio-profile-copy">
                    <strong>{profile.name}</strong>
                    <span>{profile.handle}</span>
                  </span>
                  <span className="studio-profile-check" aria-hidden="true">
                    {checked && <Check size={10} />}
                  </span>
                </button>
              );
            })}
          </div>
        </fieldset>
        <form className="studio-schedule-form" onSubmit={onSchedule}>
          <label className="studio-field-label" htmlFor={`${id}-schedule`}>
            <CalendarClock size={14} aria-hidden="true" />
            Lịch cục bộ · giờ trên thiết bị
          </label>
          <input
            type="datetime-local"
            id={`${id}-schedule`}
            value={schedule}
            required
            aria-describedby={`${id}-schedule-note`}
            onChange={(event) => {
              setSchedule(event.target.value);
              setFeedback("");
            }}
          />
          <button
            className="studio-schedule-button"
            type="submit"
            disabled={!selected.length}
          >
            <CalendarClock size={16} aria-hidden="true" />
            {selected.length
              ? `Thử lên lịch · ${selected.length} hồ sơ`
              : "Chọn ít nhất 1 hồ sơ"}
            <ArrowRight size={16} aria-hidden="true" />
          </button>
        </form>
        <div
          className="studio-schedule-feedback"
          role="status"
          aria-live="polite"
          aria-atomic="true"
        >
          {feedback && (
            <>
              <CircleCheck size={16} aria-hidden="true" />
              <span>{feedback}</span>
            </>
          )}
        </div>
        <p className="studio-truth-note" id={`${id}-schedule-note`}>
          Chỉ là demo, không kết nối tài khoản. Lịch thật cần app, máy và mạng
          hoạt động. Adapter/quyền đăng khác nhau; không bảo đảm upload live.
        </p>
      </div>
    </div>
  );
}

/** The complete local-only, state-preserving three-stage landing-page studio. */
export function StudioShowcase() {
  const [activeTab, setActiveTab] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const visible = useStudioVisible(rootRef);
  const reduced = usePrefersReducedMotion();
  const id = `studio-${useId()}`;

  const activateTab = (index: number, element: HTMLButtonElement | null) => {
    if (activeTab === index) return;
    setActiveTab(index);
    if (reduced || !element || window.SakugaFX?.calm) return;
    const bounds = element.getBoundingClientRect();
    window.SakugaFX?.puff(
      bounds.left + scrollX + bounds.width / 2,
      bounds.bottom + scrollY - 5,
      { n: 4, size: 6, vx: 0, vy: -12, spread: 1.4, life: 0.5 },
    );
  };

  const onTabKey = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % TABS.length;
    else if (event.key === "ArrowLeft")
      next = (index + TABS.length - 1) % TABS.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = TABS.length - 1;
    else return;
    event.preventDefault();
    activateTab(next, tabRefs.current[next]);
    tabRefs.current[next]?.focus({ preventScroll: true });
  };

  return (
    <MotionConfig reducedMotion="user">
      <div className="studio-showcase" ref={rootRef}>
        <div className="studio-frame">
          <div className="studio-topbar">
            <span className="studio-workbench-name">
              <span className="studio-workbench-icon">
                <Clapperboard size={15} aria-hidden="true" />
              </span>
              Creator workbench
              <span className="studio-project-name">/ song-nui.project</span>
            </span>
            <span className="studio-demo-badge">
              <Sparkles size={12} aria-hidden="true" />
              DEMO TƯƠNG TÁC
            </span>
          </div>
          <div className="studio-navigation">
            <LayoutGroup id={id}>
              <div
                role="tablist"
                aria-label="Thử ba workspace VietSub Studio"
                className="studio-tabs"
              >
                {TABS.map(({ label, icon: Icon }, index) => (
                  <button
                    type="button"
                    key={label}
                    ref={(element) => {
                      tabRefs.current[index] = element;
                    }}
                    id={`${id}-tab-${index}`}
                    role="tab"
                    aria-selected={activeTab === index}
                    aria-controls={`${id}-panel-${index}`}
                    tabIndex={activeTab === index ? 0 : -1}
                    className={`studio-tab ${activeTab === index ? "studio-tab-active" : ""}`}
                    onClick={(event) => activateTab(index, event.currentTarget)}
                    onKeyDown={(event) => onTabKey(event, index)}
                  >
                    {activeTab === index &&
                      (reduced ? (
                        <span className="studio-tab-pill" />
                      ) : (
                        <motion.span
                          className="studio-tab-pill"
                          layoutId="studio-tab-pill"
                          transition={{
                            type: "spring",
                            stiffness: 410,
                            damping: 32,
                            mass: 0.8,
                          }}
                        />
                      ))}
                    <span className="studio-tab-content">
                      <Icon size={16} aria-hidden="true" />
                      <span>{label}</span>
                      <span className="studio-tab-number">0{index + 1}</span>
                    </span>
                  </button>
                ))}
              </div>
            </LayoutGroup>
            <span className="studio-navigation-note">
              <span />
              Giữ nguyên tiến trình khi đổi tab
            </span>
          </div>
          {TABS.map((tab, index) => (
            <motion.div
              role="tabpanel"
              key={tab.label}
              id={`${id}-panel-${index}`}
              aria-labelledby={`${id}-tab-${index}`}
              hidden={activeTab !== index}
              className="studio-panel"
              initial={false}
              animate={{
                opacity: activeTab === index ? 1 : 0,
                y: activeTab === index || reduced ? 0 : 5,
              }}
              transition={{ duration: reduced ? 0 : 0.2 }}
            >
              {index === 0 && (
                <VietSubPanel
                  id={`${id}-vietsub`}
                  enabled={activeTab === 0 && visible}
                  reduced={reduced}
                />
              )}
              {index === 1 && (
                <AutoEditPanel
                  id={`${id}-edit`}
                  enabled={activeTab === 1 && visible}
                  reduced={reduced}
                />
              )}
              {index === 2 && (
                <PublishPanel
                  id={`${id}-publish`}
                  enabled={activeTab === 2 && visible}
                  reduced={reduced}
                />
              )}
            </motion.div>
          ))}
        </div>
        <p className="studio-demo-disclaimer">
          <ShieldCheck size={13} aria-hidden="true" />
          Tự tay thử, ngay trên trang. Mọi thao tác là mô phỏng cục bộ — không
          tải video, gọi AI hay đăng nội dung.
        </p>
      </div>
    </MotionConfig>
  );
}
