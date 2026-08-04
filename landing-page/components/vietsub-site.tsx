"use client";

import {
  AnimatePresence,
  MotionConfig,
  motion,
  useMotionValue,
  useReducedMotion,
  useScroll,
  useSpring,
  useTransform,
} from "framer-motion";
import {
  ArrowDown,
  ArrowRight,
  ArrowUpRight,
  Check,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  ClipboardCheck,
  Code2,
  Copy,
  Download,
  FileArchive,
  FileText,
  FolderOpen,
  GitBranch,
  HardDrive,
  Languages,
  Menu,
  Minus,
  PackageCheck,
  Play,
  Plus,
  RotateCcw,
  ScanText,
  Settings2,
  ShieldCheck,
  Sparkles,
  Upload,
  X,
  Zap,
} from "lucide-react";
import Image from "next/image";
import type { ComponentType, PointerEvent as ReactPointerEvent, ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";

const releaseUrl = "https://github.com/qvinh8726/VietSub-Studio/releases/latest";
const repoUrl = "https://github.com/qvinh8726/VietSub-Studio";

const springSoft = { type: "spring", stiffness: 260, damping: 28, mass: 0.9 } as const;
const springSnappy = { type: "spring", stiffness: 380, damping: 30, mass: 0.72 } as const;
const springBouncy = { type: "spring", stiffness: 330, damping: 21, mass: 0.78 } as const;

type IconComponent = ComponentType<{ size?: number; className?: string }>;

function CropIcon({ size = 20, className = "" }: { size?: number; className?: string }) {
  return (
    <span className={`relative inline-flex ${className}`} style={{ width: size, height: size }} aria-hidden="true">
      <span className="absolute inset-[2px] rounded-[5px] border-2 border-current" />
      <span className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-current" />
      <span className="absolute -bottom-0.5 -left-0.5 h-2 w-2 rounded-full bg-current" />
    </span>
  );
}

function QueueIcon({ size = 20, className = "" }: { size?: number; className?: string }) {
  return (
    <span
      className={`relative inline-flex flex-col justify-center gap-1 ${className}`}
      style={{ width: size, height: size }}
      aria-hidden="true"
    >
      <span className="h-1.5 w-full rounded-full bg-current" />
      <span className="h-1.5 w-[72%] rounded-full bg-current opacity-65" />
      <span className="h-1.5 w-[44%] rounded-full bg-current opacity-35" />
    </span>
  );
}

const navItems = [
  { label: "Tính năng", href: "#features" },
  { label: "Quy trình", href: "#workflow" },
  { label: "Docs", href: "#docs" },
  { label: "Hệ thống", href: "#system" },
];

const featureItems: Array<{
  number: string;
  title: string;
  text: string;
  icon: IconComponent;
  href: string;
  tone: string;
  span: string;
  spring: string;
}> = [
  {
    number: "01",
    title: "Preview vùng OCR trực quan",
    text: "Xem video trước khi chạy, kéo khung crop theo phần trăm và lưu vùng chữ phù hợp cho từng job hoặc làm mặc định.",
    icon: CropIcon,
    href: "#input",
    tone: "signal",
    span: "lg:col-span-7",
    spring: "spring(330, 21)",
  },
  {
    number: "02",
    title: "Queue tuần tự, không tranh tài nguyên",
    text: "Chuẩn bị nhiều video nhưng chỉ một worker OCR và dịch tại một thời điểm. Job lỗi giữ lại output trung gian để retry đúng bước.",
    icon: QueueIcon,
    href: "#queue",
    tone: "forest",
    span: "lg:col-span-5",
    spring: "settle(260, 28)",
  },
  {
    number: "03",
    title: "Dịch qua Notebook hoặc API",
    text: "Chọn Gemini Notebook qua Edge, DeepSeek, OpenRouter hoặc gateway OpenAI-compatible do bạn tự cấu hình.",
    icon: Languages,
    href: "#providers",
    tone: "blue",
    span: "lg:col-span-5",
    spring: "glide(0.36s)",
  },
  {
    number: "04",
    title: "SRT chỉ được chốt sau validation",
    text: "Studio kiểm tra index, số block, timestamp, nội dung và file đầu ra. Nếu model sửa timecode nhưng vẫn giữ block, app có thể khôi phục timestamp gốc.",
    icon: ClipboardCheck,
    href: "#output",
    tone: "mint",
    span: "lg:col-span-7",
    spring: "overshoot(1.04)",
  },
];

const workflowSteps = [
  {
    number: "01",
    title: "Nhận nguồn",
    short: "Douyin hoặc MP4 local",
    text: "Dán link Douyin đầy đủ, link ngắn hoặc chọn MP4 trên máy. Studio xác thực nguồn và chuẩn bị video preview.",
    icon: Upload,
  },
  {
    number: "02",
    title: "Crop và OCR",
    short: "Đúng vùng chữ, đúng ngôn ngữ",
    text: "Kéo vùng OCR, chọn ngôn ngữ và mode VideOCR. Video dài hơn 45 phút được chia thành chunk 10 phút rồi merge theo timestamp gốc.",
    icon: ScanText,
  },
  {
    number: "03",
    title: "Dịch và kiểm tra",
    short: "Provider linh hoạt, timestamp an toàn",
    text: "Dịch qua Notebook hoặc API, khôi phục timing khi cần và validate toàn bộ SRT trước khi job được đánh dấu thành công.",
    icon: Languages,
  },
  {
    number: "04",
    title: "Đóng gói project",
    short: "Một folder tự chứa",
    text: "Video, thumbnail, raw.srt, vi.srt, asset nguồn và project.json được gom vào một thư mục có thể mở lại và kiểm tra.",
    icon: PackageCheck,
  },
];

const docs = [
  { id: "quickstart", label: "Bắt đầu nhanh" },
  { id: "input", label: "Nguồn và OCR" },
  { id: "providers", label: "Provider dịch" },
  { id: "queue", label: "Queue và retry" },
  { id: "output", label: "Output và privacy" },
];

const faqs = [
  {
    q: "VietSub Studio có phải video editor không?",
    a: "Không. Đây là lớp điều phối cho workflow OCR -> dịch -> validation -> lưu project. Bạn vẫn dùng editor riêng để dựng video hoặc burn subtitle.",
  },
  {
    q: "Có cần Douzy cho mọi video không?",
    a: "Không. Douzy chỉ cần khi nguồn là Douyin. Nếu chọn MP4 local, Studio đưa file vào cache do app quản lý và chạy thẳng preview, OCR, dịch.",
  },
  {
    q: "Timestamp có bị model dịch sửa không?",
    a: "Studio luôn đối chiếu index và timestamp với SRT gốc. Nếu model chỉ thay timing nhưng vẫn giữ đúng block, app có thể khôi phục timestamp gốc trước khi validation.",
  },
  {
    q: "Tôi có thể dùng API riêng không?",
    a: "Có. Ngoài DeepSeek và OpenRouter, bạn có thể nhập Base URL, model và API key cho gateway OpenAI-compatible. HTTP chỉ được chấp nhận với địa chỉ local.",
  },
  {
    q: "Bản portable đã kèm VideOCR và FFmpeg chưa?",
    a: "Chưa. Bản portable chỉ đóng gói VietSub Studio. VideOCR, Douzy, FFmpeg, Edge và tài khoản AI là phụ thuộc bên ngoài do bạn tự cài và quản lý.",
  },
];

function Reveal({
  children,
  className = "",
  delay = 0,
  amount = 0.18,
}: {
  children: ReactNode;
  className?: string;
  delay?: number;
  amount?: number;
}) {
  return (
    <motion.div
      initial={false}
      whileInView={{ opacity: 1, y: 0, filter: "blur(0px)" }}
      viewport={{ once: true, amount }}
      transition={{ ...springSoft, delay }}
      className={className}
    >
      {children}
    </motion.div>
  );
}

function MagneticLink({
  href,
  children,
  variant = "primary",
  external = false,
  className = "",
}: {
  href: string;
  children: ReactNode;
  variant?: "primary" | "secondary" | "dark" | "ghost";
  external?: boolean;
  className?: string;
}) {
  const reduceMotion = useReducedMotion();
  const x = useMotionValue(0);
  const y = useMotionValue(0);
  const springX = useSpring(x, springBouncy);
  const springY = useSpring(y, springBouncy);

  const handlePointerMove = (event: ReactPointerEvent<HTMLAnchorElement>) => {
    if (reduceMotion) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    x.set((event.clientX - bounds.left - bounds.width / 2) * 0.16);
    y.set((event.clientY - bounds.top - bounds.height / 2) * 0.2);
  };

  const reset = () => {
    x.set(0);
    y.set(0);
  };

  const variants = {
    primary: "kinetic-button kinetic-button-primary",
    secondary: "kinetic-button kinetic-button-secondary",
    dark: "kinetic-button kinetic-button-dark",
    ghost: "kinetic-button kinetic-button-ghost",
  };

  return (
    <motion.a
      href={href}
      target={external ? "_blank" : undefined}
      rel={external ? "noreferrer" : undefined}
      onPointerMove={handlePointerMove}
      onPointerLeave={reset}
      onBlur={reset}
      whileTap={reduceMotion ? undefined : { scale: 0.95 }}
      transition={springSnappy}
      style={{ x: springX, y: springY }}
      className={`${variants[variant]} ${className}`}
    >
      <span>{children}</span>
      <ArrowUpRight size={16} aria-hidden="true" />
    </motion.a>
  );
}

function SectionHeading({
  label,
  title,
  body,
  inverse = false,
  center = false,
}: {
  label: string;
  title: string;
  body: string;
  inverse?: boolean;
  center?: boolean;
}) {
  return (
    <Reveal className={center ? "mx-auto max-w-3xl text-center" : "max-w-2xl"}>
      <p className={`section-label ${inverse ? "section-label-inverse" : ""}`}>{label}</p>
      <h2 className={`section-title ${inverse ? "text-white" : "text-[var(--ink)]"}`}>{title}</h2>
      <p className={`section-copy ${inverse ? "text-white/62" : "text-[var(--ink-soft)]"}`}>{body}</p>
    </Reveal>
  );
}

function SpringRail() {
  return (
    <motion.div
      initial={false}
      animate={{ opacity: 1, y: 0 }}
      transition={{ ...springSoft, delay: 0.06 }}
      className="spring-rail"
      aria-label="Thông số chuyển động giao diện"
    >
      <svg viewBox="0 0 1000 90" preserveAspectRatio="none" aria-hidden="true">
        <motion.path
          d="M0 47 C105 20 160 77 270 50 S420 19 530 48 S690 72 785 43 S925 29 1000 49"
          fill="none"
          stroke="var(--signal)"
          strokeWidth="2.4"
          initial={false}
          animate={{ pathLength: 1, opacity: 1 }}
          transition={{ duration: 1.15, delay: 0.2, ease: [0.16, 1, 0.3, 1] }}
        />
        <motion.path
          d="M0 40 C125 63 188 27 310 48 S470 69 588 43 S745 21 850 48 S950 60 1000 42"
          fill="none"
          stroke="var(--blue)"
          strokeOpacity="0.72"
          strokeWidth="1.4"
          initial={false}
          animate={{ pathLength: 1, opacity: 0.72 }}
          transition={{ duration: 1.25, delay: 0.3, ease: [0.16, 1, 0.3, 1] }}
        />
      </svg>
      <div className="spring-rail-readout" aria-hidden="true">
        <span>damping <strong>24</strong></span>
        <span>stiffness <strong>320</strong></span>
        <span>mass <strong>1.0</strong></span>
      </div>
    </motion.div>
  );
}

function StatusDot({ tone = "ready" }: { tone?: "ready" | "pending" | "error" }) {
  return <span className={`status-dot status-dot-${tone}`} aria-hidden="true" />;
}

function HeroPreview() {
  const reduceMotion = useReducedMotion();
  const [mode, setMode] = useState<"crop" | "queue" | "validate">("crop");
  const pointerX = useMotionValue(0.5);
  const pointerY = useMotionValue(0.5);
  const rotateY = useSpring(useTransform(pointerX, [0, 1], [-3.2, 3.2]), springSoft);
  const rotateX = useSpring(useTransform(pointerY, [0, 1], [3.2, -3.2]), springSoft);

  const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (reduceMotion) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    pointerX.set((event.clientX - bounds.left) / bounds.width);
    pointerY.set((event.clientY - bounds.top) / bounds.height);
  };

  const reset = () => {
    pointerX.set(0.5);
    pointerY.set(0.5);
  };

  return (
    <motion.div
      initial={false}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ ...springSoft, delay: 0.16 }}
      className="hero-preview-wrap"
      onPointerMove={handlePointerMove}
      onPointerLeave={reset}
      style={reduceMotion ? undefined : { rotateX, rotateY, transformPerspective: 1200 }}
    >
      <div className="hero-preview-glow" aria-hidden="true" />
      <div className="app-window">
        <div className="app-window-bar">
          <div className="flex items-center gap-2.5">
            <Image src="/avatar.jpg" width={28} height={28} alt="" className="rounded-[8px]" />
            <span className="text-xs font-extrabold">VietSub Studio</span>
          </div>
          <div className="flex items-center gap-3 text-[10px] text-white/45">
            <span>v1.5.3</span>
            <Settings2 size={14} aria-hidden="true" />
          </div>
        </div>

        <div className="preview-mode-tabs" role="tablist" aria-label="Xem trạng thái workflow">
          {[
            ["crop", "Crop OCR"],
            ["queue", "Queue"],
            ["validate", "Validate"],
          ].map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={mode === value}
              onClick={() => setMode(value as typeof mode)}
              className="preview-mode-button"
            >
              {mode === value ? (
                <motion.span layoutId="hero-preview-tab" className="preview-mode-active" transition={springSnappy} />
              ) : null}
              <span className="relative z-10">{label}</span>
            </button>
          ))}
        </div>

        <div className="app-window-content">
          <AnimatePresence mode="wait" initial={false}>
            {mode === "crop" ? (
              <motion.div
                key="crop"
                initial={false}
                animate={{ opacity: 1, x: 0 }}
                exit={reduceMotion ? undefined : { opacity: 0, x: -12 }}
                transition={springSnappy}
                className="preview-grid"
              >
                <div className="video-panel">
                  <div className="panel-heading">
                    <div>
                      <p>Preview vùng OCR</p>
                      <strong>douyin_product_review.mp4</strong>
                    </div>
                    <span><StatusDot /> sẵn sàng</span>
                  </div>
                  <div className="video-frame">
                    <div className="video-scanline" aria-hidden="true" />
                    <div className="video-light video-light-one" aria-hidden="true" />
                    <div className="video-light video-light-two" aria-hidden="true" />
                    <motion.div
                      className="ocr-box"
                      initial={false}
                      animate={{ scale: 1, opacity: 1 }}
                      transition={{ ...springBouncy, delay: 0.08 }}
                    >
                      <span>VÙNG OCR</span>
                      <i />
                    </motion.div>
                    <div className="video-time">00:21 / 01:08</div>
                  </div>
                  <div className="crop-values">
                    {[
                      ["X", "12.0"],
                      ["Y", "44.0"],
                      ["Rộng", "68.0"],
                      ["Cao", "29.0"],
                    ].map(([label, value]) => (
                      <div key={label}>
                        <span>{label} (%)</span>
                        <strong>{value}</strong>
                      </div>
                    ))}
                  </div>
                </div>
                <div className="queue-panel">
                  <div className="queue-panel-top">
                    <div>
                      <p>Hàng đợi</p>
                      <strong>03</strong>
                    </div>
                    <QueueIcon size={28} className="text-[var(--signal)]" />
                  </div>
                  <div className="queue-list">
                    <div><span>review_01.mp4</span><strong>OCR</strong></div>
                    <div><span>review_02.mp4</span><em>queued</em></div>
                    <div><span>review_03.mp4</span><em>queued</em></div>
                  </div>
                  <div className="queue-progress">
                    <div className="flex items-center justify-between">
                      <span>VideOCR</span>
                      <strong>48%</strong>
                    </div>
                    <div className="queue-progress-track">
                      <motion.span
                        animate={reduceMotion ? { width: "48%" } : { width: ["22%", "72%", "48%"] }}
                        transition={{ duration: 3, repeat: reduceMotion ? 0 : Infinity, ease: "easeInOut" }}
                      />
                    </div>
                  </div>
                </div>
              </motion.div>
            ) : null}

            {mode === "queue" ? (
              <motion.div
                key="queue"
                initial={false}
                animate={{ opacity: 1, y: 0 }}
                exit={reduceMotion ? undefined : { opacity: 0, y: -10 }}
                transition={springSnappy}
                className="queue-mode"
              >
                <div className="queue-mode-head">
                  <div>
                    <p>Workflow queue</p>
                    <h3>Một worker. Ba job đã sẵn sàng.</h3>
                  </div>
                  <span><StatusDot /> worker online</span>
                </div>
                <div className="queue-mode-list">
                  {[
                    ["01", "review_01.mp4", "OCR đang chạy", "48%"],
                    ["02", "review_02.mp4", "Đã chuẩn bị preview", "queued"],
                    ["03", "review_03.mp4", "Chờ worker", "queued"],
                  ].map(([number, file, status, value], index) => (
                    <motion.div
                      key={file}
                      initial={false}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ ...springSoft, delay: index * 0.06 }}
                    >
                      <span>{number}</span>
                      <div><strong>{file}</strong><em>{status}</em></div>
                      <b className={index === 0 ? "is-running" : ""}>{value}</b>
                    </motion.div>
                  ))}
                </div>
              </motion.div>
            ) : null}

            {mode === "validate" ? (
              <motion.div
                key="validate"
                initial={false}
                animate={{ opacity: 1, scale: 1 }}
                exit={reduceMotion ? undefined : { opacity: 0, scale: 0.98 }}
                transition={springBouncy}
                className="validate-mode"
              >
                <motion.div
                  initial={false}
                  animate={{ scale: 1, rotate: 0 }}
                  transition={{ ...springBouncy, delay: 0.08 }}
                  className="validate-check"
                >
                  <CircleCheck size={32} aria-hidden="true" />
                </motion.div>
                <p>SRT validation</p>
                <h3>12/12 block đã khóa timestamp.</h3>
                <div className="validate-grid">
                  <div><span>Index</span><strong>Khớp</strong></div>
                  <div><span>Timecode</span><strong>Giữ nguyên</strong></div>
                  <div><span>Output</span><strong>vi.srt</strong></div>
                </div>
              </motion.div>
            ) : null}
          </AnimatePresence>
        </div>

        <div className="app-window-footer">
          <span><StatusDot /> local workflow</span>
          <span>{"resolve -> download -> ocr -> translate -> done"}</span>
        </div>
      </div>
    </motion.div>
  );
}

function ToolRail() {
  const tools = ["Douyin / MP4", "VideOCR", "Gemini Notebook", "DeepSeek / OpenRouter", "SRT validation"];

  return (
    <div className="tool-rail" aria-label="Các công cụ trong pipeline">
      <div className="tool-rail-track">
        {[0, 1].map((group) => (
          <div className="tool-rail-group" aria-hidden={group === 1} key={group}>
            {tools.map((tool) => (
              <span key={`${group}-${tool}`}><i />{tool}</span>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

function FeatureCard({ item, index }: { item: (typeof featureItems)[number]; index: number }) {
  const reduceMotion = useReducedMotion();
  const Icon = item.icon;

  const handlePointerMove = (event: ReactPointerEvent<HTMLElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    event.currentTarget.style.setProperty("--spot-x", `${event.clientX - bounds.left}px`);
    event.currentTarget.style.setProperty("--spot-y", `${event.clientY - bounds.top}px`);
  };

  return (
    <motion.article
      initial={false}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.2 }}
      whileHover={reduceMotion ? undefined : { y: -9, scale: 1.008 }}
      transition={{ ...springSoft, delay: index * 0.04 }}
      onPointerMove={handlePointerMove}
      className={`feature-card feature-card-${item.tone} ${item.span}`}
    >
      <div className="feature-card-spotlight" aria-hidden="true" />
      <div className="relative z-10 flex h-full flex-col">
        <div className="flex items-start justify-between gap-4">
          <div className="feature-icon"><Icon size={22} /></div>
          <span className="motion-readout">{item.spring}</span>
        </div>
        <div className="mt-auto pt-16 sm:pt-24">
          <span className="feature-number">{item.number}</span>
          <h3>{item.title}</h3>
          <p>{item.text}</p>
          <a href={item.href} className="feature-link">
            Đọc trong docs <ArrowUpRight size={15} aria-hidden="true" />
          </a>
        </div>
      </div>
    </motion.article>
  );
}

function WorkflowVisual({ active }: { active: number }) {
  const reduceMotion = useReducedMotion();
  const step = workflowSteps[active];
  const StepIcon = step.icon;

  return (
    <div className="workflow-visual">
      <div className="workflow-visual-head">
        <span>// live workflow state</span>
        <span><StatusDot /> worker ready</span>
      </div>
      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={step.number}
          initial={false}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={reduceMotion ? undefined : { opacity: 0, y: -12, scale: 0.99 }}
          transition={springSnappy}
          className="workflow-stage"
        >
          <div className="workflow-stage-icon"><StepIcon size={26} aria-hidden="true" /></div>
          <p>stage {step.number} / 04</p>
          <h3>{step.title}</h3>
          <span>{step.short}</span>

          {active === 0 ? (
            <div className="workflow-source-grid">
              <div><Upload size={17} /><strong>Douyin URL</strong><span>đã xác thực</span></div>
              <div><FolderOpen size={17} /><strong>MP4 local</strong><span>tối đa 8 GiB</span></div>
            </div>
          ) : null}

          {active === 1 ? (
            <div className="workflow-crop-mini">
              <div className="workflow-crop-frame"><span>subtitle region</span></div>
              <div className="workflow-crop-meta"><span>ch / accurate</span><strong>68% x 29%</strong></div>
            </div>
          ) : null}

          {active === 2 ? (
            <div className="workflow-translation">
              <div><span>00:00:14</span><p>大家好, 今天我们来看看这个产品。</p></div>
              <ArrowDown size={16} />
              <div className="translated"><span>00:00:14</span><p>Xin chào, hôm nay chúng ta sẽ xem sản phẩm này.</p></div>
            </div>
          ) : null}

          {active === 3 ? (
            <div className="workflow-output-list">
              {["product-review.mp4", "product-review.raw.srt", "product-review.vi.srt", "project.json"].map((file, index) => (
                <motion.div
                  key={file}
                  initial={false}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ ...springSoft, delay: index * 0.055 }}
                >
                  {index === 3 ? <FileText size={15} /> : <FileArchive size={15} />}
                  <span>{file}</span>
                  <Check size={14} />
                </motion.div>
              ))}
            </div>
          ) : null}
        </motion.div>
      </AnimatePresence>

      <div className="workflow-progress" aria-label={`Đang xem bước ${active + 1} trên 4`}>
        {workflowSteps.map((item, index) => (
          <span key={item.number} className={index <= active ? "is-complete" : ""} />
        ))}
      </div>
    </div>
  );
}

function DocsSection({ id, label, title, children }: { id: string; label: string; title: string; children: ReactNode }) {
  return (
    <article id={id} className="docs-article scroll-mt-32">
      <p>{label}</p>
      <h3>{title}</h3>
      <div className="docs-prose">{children}</div>
    </article>
  );
}

function MiniDocCard({ icon: Icon, title, body }: { icon: IconComponent; title: string; body: string }) {
  return (
    <div className="mini-doc-card">
      <Icon size={17} aria-hidden="true" />
      <strong>{title}</strong>
      <span>{body}</span>
    </div>
  );
}

function CodeBlock() {
  const [copied, setCopied] = useState(false);
  const code = `project/\n|-- product-review.mp4\n|-- product-review.raw.srt\n|-- product-review.vi.srt\n\`-- project.json`;

  const copy = () => {
    setCopied(true);
    window.setTimeout(() => setCopied(false), 5000);
    if (navigator.clipboard) {
      void navigator.clipboard.writeText(code).catch(() => undefined);
    }
  };

  return (
    <div className="code-block">
      <button type="button" aria-label="Sao chép cấu trúc thư mục output" onClick={copy}>
        <span>{copied ? <Check size={13} /> : <Copy size={13} />}{copied ? "Đã copy" : "Copy"}</span>
      </button>
      <pre><code><span># Output sau validation</span>{"\n"}{code}</code></pre>
    </div>
  );
}

function RequirementCard() {
  return (
    <Reveal className="requirement-card">
      <div className="requirement-card-head">
        <div>
          <p>// bản portable</p>
          <h3>VietSub Studio v1.5.3</h3>
          <span>Windows-first. Mã nguồn mở. Cài từ GitHub Releases.</span>
        </div>
        <Image src="/avatar.jpg" width={62} height={62} alt="Biểu tượng VietSub Studio" className="requirement-logo" />
      </div>
      <div className="requirement-grid">
        <div>
          <p>Bắt buộc</p>
          <ul>
            <li><Check size={14} /> Windows 10/11 x64</li>
            <li><Check size={14} /> VideOCR CLI</li>
            <li><Check size={14} /> Provider dịch bạn chọn</li>
          </ul>
        </div>
        <div>
          <p>Khuyến nghị</p>
          <ul>
            <li><Check size={14} /> FFmpeg + FFprobe</li>
            <li><Check size={14} /> Douzy cho Douyin</li>
            <li><Check size={14} /> Edge cho Notebook</li>
          </ul>
        </div>
      </div>
      <MagneticLink href={releaseUrl} external variant="dark" className="mt-6 w-full justify-between">
        Tải bản portable mới nhất
      </MagneticLink>
    </Reveal>
  );
}

export default function VietSubSite() {
  const reduceMotion = useReducedMotion();
  const [menuOpen, setMenuOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const [activeDoc, setActiveDoc] = useState("quickstart");
  const [activeWorkflow, setActiveWorkflow] = useState(0);
  const [openFaq, setOpenFaq] = useState<number | null>(0);
  const { scrollYProgress } = useScroll();
  const smoothProgress = useSpring(scrollYProgress, { stiffness: 150, damping: 30, mass: 0.22 });

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 18);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio);
        if (visible[0]) setActiveDoc(visible[0].target.id);
      },
      { rootMargin: "-18% 0px -62% 0px", threshold: [0.1, 0.35, 0.7] },
    );

    docs.forEach(({ id }) => {
      const node = document.getElementById(id);
      if (node) observer.observe(node);
    });

    return () => observer.disconnect();
  }, []);

  const activeDocLabel = useMemo(
    () => docs.find((item) => item.id === activeDoc)?.label ?? docs[0].label,
    [activeDoc],
  );

  return (
    <MotionConfig reducedMotion="user">
      <main className="site-shell">
        <motion.div className="scroll-progress" style={{ scaleX: smoothProgress }} aria-hidden="true" />

        <header className={`site-header ${scrolled ? "site-header-scrolled" : ""}`}>
          <div className="nav-shell">
            <a href="#top" className="brand-link" aria-label="Về đầu trang VietSub Studio">
              <span className="brand-mark">
                <Image src="/avatar.jpg" width={34} height={34} alt="" />
              </span>
              <span>VietSub Studio</span>
            </a>

            <nav className="desktop-nav" aria-label="Điều hướng chính">
              {navItems.map((item) => (
                <a href={item.href} key={item.href}>{item.label}</a>
              ))}
            </nav>

            <div className="desktop-actions">
              <a href={repoUrl} target="_blank" rel="noreferrer" className="github-link">
                <GitBranch size={15} aria-hidden="true" /> GitHub
              </a>
              <MagneticLink href={releaseUrl} external variant="primary">Tải portable</MagneticLink>
            </div>

            <button
              type="button"
              aria-label={menuOpen ? "Đóng menu" : "Mở menu"}
              aria-expanded={menuOpen}
              aria-controls="mobile-menu"
              onClick={() => setMenuOpen((value) => !value)}
              className="mobile-menu-button"
            >
              {menuOpen ? <X size={20} /> : <Menu size={20} />}
            </button>
          </div>

          <AnimatePresence initial={false}>
            {menuOpen ? (
              <motion.nav
                id="mobile-menu"
                initial={false}
                animate={{ opacity: 1, height: "auto", y: 0 }}
                exit={reduceMotion ? undefined : { opacity: 0, height: 0, y: -6 }}
                transition={springSnappy}
                className="mobile-nav"
                aria-label="Điều hướng di động"
              >
                <div>
                  {navItems.map((item) => (
                    <a href={item.href} key={item.href} onClick={() => setMenuOpen(false)}>{item.label}</a>
                  ))}
                  <a href={releaseUrl} target="_blank" rel="noreferrer" onClick={() => setMenuOpen(false)}>
                    Tải bản portable <Download size={16} />
                  </a>
                </div>
              </motion.nav>
            ) : null}
          </AnimatePresence>
        </header>

        <section id="top" className="hero-section">
          <div className="hero-grid-bg" aria-hidden="true" />
          <div className="hero-noise" aria-hidden="true" />
          <div className="page-container relative z-10">
            <SpringRail />
            <div className="hero-layout">
              <motion.div
                initial={false}
                animate={{ opacity: 1, y: 0 }}
                transition={{ ...springSoft, delay: 0.08 }}
                className="hero-copy-block"
              >
                <p className="hero-label">// OCR PHỤ ĐỀ CHÁY TRÊN VIDEO</p>
                <h1>
                  TỪ VIDEO
                  <span>ĐẾN SRT.</span>
                </h1>
                <p className="hero-description">
                  VietSub Studio điều phối trọn workflow: nhận Douyin hoặc MP4, crop đúng vùng chữ,
                  chạy VideOCR, dịch sang tiếng Việt và giữ nguyên timestamp trước khi lưu project.
                </p>
                <div className="hero-actions">
                  <MagneticLink href={releaseUrl} external variant="primary">Tải bản portable</MagneticLink>
                  <a href="#workflow" className="text-action">
                    <span className="text-action-icon"><Play size={14} fill="currentColor" /></span>
                    Xem quy trình
                    <ArrowRight size={15} />
                  </a>
                </div>
                <div className="hero-proof">
                  <span><Check size={14} /> Windows 10/11</span>
                  <span><Check size={14} /> MIT license</span>
                  <span><Check size={14} /> OpenAI-compatible</span>
                </div>
              </motion.div>
              <HeroPreview />
            </div>
            <ToolRail />
          </div>
        </section>

        <section id="features" className="section-space feature-section">
          <div className="page-container">
            <div className="section-head-row">
              <SectionHeading
                label="// TÍNH NĂNG CỐT LÕI"
                title="Workflow có trọng lượng. Mỗi trạng thái đều rõ ràng."
                body="Studio không cố thay thế mọi công cụ. Nó giữ phần điều phối để OCR, dịch, validation và file output không còn rời rạc."
              />
              <Reveal className="section-count" delay={0.08}>
                <span>04</span>
                <p>khối chức năng<br />được nối thành một flow</p>
              </Reveal>
            </div>
            <div className="feature-grid">
              {featureItems.map((item, index) => <FeatureCard item={item} index={index} key={item.number} />)}
            </div>
          </div>
        </section>

        <section id="workflow" className="section-space workflow-section">
          <div className="workflow-grid-bg" aria-hidden="true" />
          <div className="page-container relative z-10">
            <SectionHeading
              inverse
              label="// CÁCH HOẠT ĐỘNG"
              title="Bốn bước. Một trạng thái có thể tiếp tục."
              body="Mỗi job biết mình đang ở đâu, giữ lại output trung gian và retry từ điểm hợp lý nhất thay vì bắt đầu lại toàn bộ."
            />
            <div className="workflow-layout">
              <div className="workflow-step-list" role="tablist" aria-label="Các bước xử lý">
                {workflowSteps.map((step, index) => {
                  const Icon = step.icon;
                  const active = activeWorkflow === index;
                  return (
                    <button
                      type="button"
                      role="tab"
                      aria-selected={active}
                      key={step.number}
                      onClick={() => setActiveWorkflow(index)}
                      className="workflow-step"
                    >
                      {active ? <motion.span layoutId="workflow-active" className="workflow-step-active" transition={springSnappy} /> : null}
                      <span className="workflow-step-number">{step.number}</span>
                      <span className="workflow-step-content">
                        <strong><Icon size={18} />{step.title}</strong>
                        <em>{step.text}</em>
                      </span>
                      <ChevronRight size={18} className="workflow-step-arrow" />
                    </button>
                  );
                })}
              </div>
              <Reveal amount={0.12}><WorkflowVisual active={activeWorkflow} /></Reveal>
            </div>
          </div>
        </section>

        <section id="docs" className="section-space docs-section">
          <div className="page-container">
            <div className="docs-heading-row">
              <SectionHeading
                label="// DOCS / WORKFLOW"
                title="Không cần đoán tool nào chạy trước."
                body="Tài liệu cô đọng để cài phụ thuộc, chọn provider và hiểu chính xác output mà VietSub Studio tạo ra."
              />
              <Reveal className="docs-now-viewing" delay={0.08}>
                <span>Đang xem</span>
                <strong>{activeDocLabel}</strong>
              </Reveal>
            </div>

            <div className="docs-layout">
              <aside className="docs-sidebar">
                <p>Mục lục</p>
                <nav aria-label="Mục lục tài liệu">
                  {docs.map((item) => (
                    <a href={`#${item.id}`} key={item.id} className={activeDoc === item.id ? "is-active" : ""}>
                      {activeDoc === item.id ? <motion.span layoutId="docs-active" transition={springSnappy} /> : null}
                      <em>{item.label}</em>
                      <ChevronRight size={15} />
                    </a>
                  ))}
                </nav>
                <div className="docs-github-card">
                  <GitBranch size={16} />
                  <strong>Open source</strong>
                  <p>Release notes, source code và bug report đều nằm trên GitHub.</p>
                  <a href={repoUrl} target="_blank" rel="noreferrer">Mở repository <ArrowUpRight size={13} /></a>
                </div>
              </aside>

              <div className="docs-content">
                <DocsSection id="quickstart" label="01 / BẮT ĐẦU NHANH" title="Cài bản portable rồi mở Studio">
                  <p>
                    Tải asset <code>VietSub-Studio-Portable-v*.zip</code> từ GitHub Releases, giải nén vào thư mục
                    có quyền ghi và mở <code>VietSub Studio.exe</code>. Bản portable chỉ đóng gói ứng dụng điều phối;
                    VideOCR, Douzy, FFmpeg, Edge và tài khoản AI vẫn là phụ thuộc bên ngoài.
                  </p>
                  <div className="mini-doc-grid three">
                    <MiniDocCard icon={Download} title="1. Tải app" body="Windows 10/11 x64" />
                    <MiniDocCard icon={Settings2} title="2. Cài tool" body="VideOCR và provider" />
                    <MiniDocCard icon={Play} title="3. Chạy flow" body="Preview -> queue -> run" />
                  </div>
                </DocsSection>

                <DocsSection id="input" label="02 / NGUỒN VÀ OCR" title="Preview trước khi để OCR chạy">
                  <p>
                    Dán URL Douyin đầy đủ, link ngắn <code>v.douyin.com</code> hoặc chọn MP4 local. Studio hiển thị
                    preview, cho phép kéo crop theo phần trăm và lưu crop mặc định. OCR hỗ trợ <code>ch</code>,
                    <code>en</code>, <code>ja</code>, <code>ko</code>, <code>vi</code> cùng các mode <code>auto</code>,
                    <code>fast</code>, <code>accurate</code>.
                  </p>
                  <div className="docs-callout docs-callout-tip">
                    <CropIcon size={17} />
                    <div><strong>Tip</strong><span>Video dài hơn 45 phút được tách thành chunk OCR 10 phút rồi merge theo timestamp gốc.</span></div>
                  </div>
                </DocsSection>

                <DocsSection id="providers" label="03 / PROVIDER DỊCH" title="Chọn cách dịch phù hợp với setup">
                  <p>
                    Gemini Notebook chạy qua Microsoft Edge với profile debug riêng. Nếu muốn API, chọn DeepSeek,
                    OpenRouter hoặc Custom rồi nhập Base URL, model và API key. API ưu tiên streaming SSE, tự fallback
                    JSON và retry tối đa ba lần cho lỗi tạm thời.
                  </p>
                  <div className="mini-doc-grid two">
                    <MiniDocCard icon={Languages} title="Gemini Notebook" body="Qua Edge / Playwright" />
                    <MiniDocCard icon={Code2} title="API compatible" body="DeepSeek, OpenRouter, custom" />
                  </div>
                </DocsSection>

                <DocsSection id="queue" label="04 / QUEUE VÀ RETRY" title="Lỗi ở đâu, retry đúng từ đó">
                  <p>
                    Job có trạng thái <code>queued</code>, <code>running</code>, <code>success</code>, <code>error</code>,
                    <code>cancelled</code>. Worker chạy tuần tự và giữ log cùng output trung gian, nên lỗi dịch không buộc
                    bạn download và OCR lại từ đầu.
                  </p>
                  <div className="retry-flow" aria-label="Ví dụ retry từ bước dịch">
                    <span className="success">OCR success</span><ArrowRight size={14} />
                    <span className="error">Translate error</span><ArrowRight size={14} />
                    <span className="pending">Retry translate</span>
                  </div>
                </DocsSection>

                <DocsSection id="output" label="05 / OUTPUT VÀ PRIVACY" title="Một folder cho mỗi project">
                  <p>
                    Sau validation, Studio lưu video, thumbnail nếu có, <code>.raw.srt</code>, <code>.vi.srt</code>,
                    asset Douzy và <code>project.json</code> vào một folder riêng. App bind Flask trên
                    <code>127.0.0.1</code>, lưu config local và cảnh báo nếu output nằm trong OneDrive.
                  </p>
                  <CodeBlock />
                  <div className="docs-callout docs-callout-note">
                    <ShieldCheck size={17} />
                    <div><strong>Rõ ràng về dữ liệu</strong><span>Bạn tự chọn dịch vụ AI, tự quản lý credential và toàn bộ output local.</span></div>
                  </div>
                </DocsSection>
              </div>
            </div>
          </div>
        </section>

        <section id="system" className="section-space system-section">
          <div className="system-orbit system-orbit-one" aria-hidden="true" />
          <div className="system-orbit system-orbit-two" aria-hidden="true" />
          <div className="page-container relative z-10">
            <div className="system-layout">
              <div>
                <SectionHeading
                  inverse
                  label="// TẢI VÀ TRIỂN KHAI"
                  title="Không pricing wall. Chỉ có workflow rõ ràng."
                  body="VietSub Studio là dự án MIT. Tải bản portable, cài đúng những phụ thuộc bạn cần và bắt đầu từ một video ngắn để kiểm tra setup."
                />
                <Reveal className="benefit-list" delay={0.06}>
                  <div><Zap size={18} /><span><strong>Retry có ngữ cảnh</strong>Không chạy lại những bước đã có output hợp lệ.</span></div>
                  <div><ShieldCheck size={18} /><span><strong>Local-first</strong>Server chỉ bind loopback và config nằm trên máy.</span></div>
                  <div><HardDrive size={18} /><span><strong>Project tự chứa</strong>Dễ mở lại, kiểm tra và đưa sang editor khác.</span></div>
                </Reveal>
                <Reveal className="system-actions" delay={0.1}>
                  <MagneticLink href={releaseUrl} external variant="primary">Tải release mới nhất</MagneticLink>
                  <MagneticLink href={repoUrl} external variant="ghost">Xem source GitHub</MagneticLink>
                </Reveal>
              </div>
              <RequirementCard />
            </div>
          </div>
        </section>

        <section id="faq" className="section-space faq-section">
          <div className="page-container faq-layout">
            <div>
              <SectionHeading
                label="// FAQ"
                title="Những câu hỏi trước job đầu tiên."
                body="Các giới hạn quan trọng được nói thẳng để bạn biết Studio làm gì, cần cài gì và dữ liệu đi qua đâu."
              />
              <Reveal className="faq-note" delay={0.08}>
                <CircleAlert size={17} />
                <span>Bản portable không kèm tool bên thứ ba hoặc tài khoản AI.</span>
              </Reveal>
            </div>
            <Reveal className="faq-list" amount={0.1}>
              {faqs.map((faq, index) => {
                const open = openFaq === index;
                return (
                  <div className="faq-item" key={faq.q}>
                    <button type="button" aria-expanded={open} onClick={() => setOpenFaq(open ? null : index)}>
                      <span>{faq.q}</span>
                      <motion.i animate={{ rotate: open ? 180 : 0 }} transition={springSnappy}>
                        {open ? <Minus size={15} /> : <Plus size={15} />}
                      </motion.i>
                    </button>
                    <AnimatePresence initial={false}>
                      {open ? (
                        <motion.div
                          initial={false}
                          animate={{ height: "auto", opacity: 1 }}
                          exit={reduceMotion ? undefined : { height: 0, opacity: 0 }}
                          transition={springSoft}
                          className="faq-answer"
                        >
                          <p>{faq.a}</p>
                        </motion.div>
                      ) : null}
                    </AnimatePresence>
                  </div>
                );
              })}
            </Reveal>
          </div>
        </section>

        <section className="final-cta-section">
          <div className="page-container">
            <Reveal className="final-cta" amount={0.18}>
              <div className="final-cta-grid" aria-hidden="true" />
              <div className="final-cta-ring" aria-hidden="true" />
              <div className="relative z-10">
                <p>// BẮT ĐẦU TỪ VIDEO KẾ TIẾP</p>
                <h2>OCR đúng vùng.<br />Dịch đúng block.<br /><span>Lưu đúng project.</span></h2>
              </div>
              <div className="relative z-10 final-cta-action">
                <MagneticLink href={releaseUrl} external variant="dark">Tải VietSub Studio</MagneticLink>
                <span>Windows 10/11 x64 · MIT license</span>
              </div>
            </Reveal>
          </div>
        </section>

        <footer className="site-footer">
          <div className="page-container">
            <div className="footer-grid">
              <div>
                <a href="#top" className="brand-link">
                  <span className="brand-mark"><Image src="/avatar.jpg" width={34} height={34} alt="" /></span>
                  <span>VietSub Studio</span>
                </a>
                <p>Lớp điều phối mã nguồn mở cho workflow OCR phụ đề và dịch tiếng Việt trên Windows.</p>
              </div>
              <div><strong>Đi tới</strong><a href="#features">Tính năng</a><a href="#workflow">Quy trình</a><a href="#docs">Docs</a></div>
              <div><strong>Project</strong><a href={repoUrl} target="_blank" rel="noreferrer">GitHub</a><a href={releaseUrl} target="_blank" rel="noreferrer">Releases</a><a href="#faq">FAQ</a></div>
              <div><strong>Stack</strong><span>Python + Flask</span><span>VideOCR + FFmpeg</span><span>MIT License</span></div>
            </div>
            <div className="footer-bottom">
              <span>(c) 2026 VietSub Studio. Open-source workflow, Windows-first.</span>
              <span>v1.5.3 / motion tuned with spring physics</span>
            </div>
          </div>
        </footer>
      </main>
    </MotionConfig>
  );
}
