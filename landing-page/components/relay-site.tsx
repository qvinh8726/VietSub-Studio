"use client";

import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowDown,
  ArrowRight,
  ArrowUpRight,
  Check,
  ChevronDown,
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
  Settings2,
  ShieldCheck,
  Sparkles,
  Upload,
  X,
  Zap,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

const Github = GitBranch;

const releaseUrl = "https://github.com/qvinh8726/VietSub-Studio/releases/latest";
const repoUrl = "https://github.com/qvinh8726/VietSub-Studio";

const navItems = [
  { label: "Tính năng", href: "#features" },
  { label: "Quy trình", href: "#workflow" },
  { label: "Docs", href: "#docs" },
  { label: "Hệ thống", href: "#system" },
];

const featureItems = [
  {
    number: "01",
    title: "Preview vùng OCR",
    text: "Xem video trước, kéo khung vàng vào đúng vùng chữ và lưu crop mặc định cho những video tiếp theo.",
    icon: CropIcon,
    accent: "sun",
  },
  {
    number: "02",
    title: "Hàng đợi tuần tự",
    text: "Nhiều video được chuẩn bị trước nhưng chỉ một worker chạy OCR và dịch tại một thời điểm để tránh tranh chấp tài nguyên.",
    icon: QueueIcon,
    accent: "forest",
  },
  {
    number: "03",
    title: "Dịch bằng nhiều provider",
    text: "Gemini Notebook qua Edge, DeepSeek, OpenRouter hoặc bất kỳ API OpenAI-compatible nào bạn tự cấu hình.",
    icon: Languages,
    accent: "blue",
  },
  {
    number: "04",
    title: "Validate trước khi xong",
    text: "Index, block, timestamp và nội dung SRT đều được kiểm tra trước khi job chuyển sang thành công.",
    icon: ClipboardCheck,
    accent: "mint",
  },
];

const workflowSteps = [
  { number: "01", title: "Nhận nguồn", text: "Dán link Douyin đầy đủ, link ngắn hoặc chọn MP4 local. Studio kiểm tra URL và chuẩn bị preview.", icon: Upload },
  { number: "02", title: "OCR và dịch", text: "Chọn ngôn ngữ, crop vùng subtitle, để VideOCR tạo raw.srt rồi dịch bằng Notebook hoặc API.", icon: Languages },
  { number: "03", title: "Đóng gói project", text: "Giữ nguyên timestamp, validate output và lưu video, thumbnail, raw.srt, vi.srt cùng manifest riêng.", icon: PackageCheck },
];

const docs = [
  { id: "quickstart", label: "Bắt đầu nhanh" },
  { id: "input", label: "Nguồn và OCR" },
  { id: "providers", label: "Provider dịch" },
  { id: "queue", label: "Queue và retry" },
  { id: "output", label: "Output và privacy" },
];

const faqs = [
  { q: "VietSub Studio có phải video editor không?", a: "Không. Đây là lớp điều phối cho workflow OCR -> dịch -> validation -> lưu project. Bạn vẫn dùng editor riêng để dựng video." },
  { q: "Có cần Douzy không?", a: "Chỉ khi nguồn là video Douyin. Nếu bạn chọn MP4 local, workflow không cần Douzy; VideOCR vẫn cần cho bước OCR." },
  { q: "Timestamp có bị model dịch sửa không?", a: "Studio giữ timestamp và index từ SRT gốc, sau đó validate block, timecode và nội dung trước khi coi là thành công." },
  { q: "Tôi có thể dùng API riêng không?", a: "Có. Ngoài Gemini Notebook, DeepSeek và OpenRouter, bạn có thể nhập Base URL, model và API key cho gateway OpenAI-compatible." },
];

const reveal = {
  hidden: { opacity: 0, y: 22 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.65, ease: [0.16, 1, 0.3, 1] as [number, number, number, number] } },
};

function CropIcon({ size = 20 }: { size?: number }) {
  return <span className="relative inline-flex" style={{ width: size, height: size }}><span className="absolute inset-0 rounded-[5px] border-2 border-current" /><span className="absolute -right-1 -top-1 h-2 w-2 rounded-full bg-current" /><span className="absolute -bottom-1 -left-1 h-2 w-2 rounded-full bg-current" /></span>;
}

function QueueIcon({ size = 20 }: { size?: number }) {
  return <span className="relative inline-flex flex-col gap-1" style={{ width: size, height: size }}><span className="h-1.5 w-4 rounded-full bg-current" /><span className="h-1.5 w-3 rounded-full bg-current opacity-65" /><span className="h-1.5 w-2 rounded-full bg-current opacity-35" /></span>;
}

function SectionHeading({ eyebrow, title, body, center = false, inverse = false }: { eyebrow: string; title: string; body: string; center?: boolean; inverse?: boolean }) {
  return <motion.div variants={reveal} initial="hidden" whileInView="visible" viewport={{ once: true, amount: 0.25 }} className={center ? "mx-auto max-w-3xl text-center" : "max-w-2xl"}><p className="section-kicker mb-4">{eyebrow}</p><h2 className={`display-font text-4xl font-semibold tracking-[-0.06em] sm:text-5xl ${inverse ? "text-white" : "text-ink"}`}>{title}</h2><p className={`mt-5 text-base leading-8 sm:text-lg ${inverse ? "text-white/65" : "text-[var(--ink-soft)]"}`}>{body}</p></motion.div>;
}

function ButtonLink({ href, children, variant = "primary", external = false }: { href: string; children: React.ReactNode; variant?: "primary" | "secondary" | "dark"; external?: boolean }) {
  const className = variant === "primary" ? "bg-[var(--forest)] text-white shadow-[0_10px_0_#0d2e27] hover:-translate-y-0.5 hover:bg-[var(--forest-2)] hover:shadow-[0_12px_0_#0d2e27]" : variant === "dark" ? "bg-[var(--ink)] text-white hover:bg-[var(--forest)]" : "border border-[var(--line)] bg-white/55 text-ink hover:border-[var(--forest-2)] hover:bg-white";
  return <a href={href} target={external ? "_blank" : undefined} rel={external ? "noreferrer" : undefined} className={`focus-ring group inline-flex items-center justify-center gap-3 rounded-xl px-5 py-3 text-sm font-extrabold transition duration-300 ${className}`}>{children}<ArrowUpRight size={16} className="transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5" /></a>;
}

function StatusPill({ label, tone = "ready" }: { label: string; tone?: "ready" | "pending" | "warn" }) {
  const dot = tone === "ready" ? "bg-[var(--forest-2)] shadow-[0_0_0_4px_rgba(36,94,79,.13)]" : tone === "warn" ? "bg-[var(--coral)] shadow-[0_0_0_4px_rgba(152,59,46,.13)]" : "bg-[var(--sun)] shadow-[0_0_0_4px_rgba(237,174,73,.14)]";
  return <span className="inline-flex items-center gap-2 rounded-full border border-[var(--line)] bg-white/65 px-3 py-2 text-[11px] font-bold text-[var(--ink-soft)]"><i className={`h-2 w-2 rounded-full ${dot}`} />{label}</span>;
}

function StudioPreview() {
  return <motion.div variants={reveal} initial="hidden" whileInView="visible" viewport={{ once: true, amount: 0.15 }} className="relative mx-auto w-full max-w-[650px]"><div className="absolute -inset-10 rounded-[3rem] bg-[radial-gradient(circle_at_70%_20%,rgba(36,94,79,.25),transparent_42%),radial-gradient(circle_at_10%_85%,rgba(237,174,73,.27),transparent_44%)] blur-3xl" /><div className="relative overflow-hidden rounded-[28px] border border-[#c8bca8] bg-[var(--paper)] p-3 shadow-[var(--shadow-soft)]"><div className="flex items-center justify-between rounded-[18px] bg-[var(--forest)] px-4 py-3 text-white"><span className="flex items-center gap-2 text-xs font-bold"><img src="/avatar.jpg" alt="" className="h-6 w-6 rounded-lg object-cover" />VietSub Studio</span><span className="flex items-center gap-3 text-[10px] text-white/60"><span>v1.5.3</span><Settings2 size={14} /></span></div><div className="grid gap-3 p-3 lg:grid-cols-[1.25fr_.75fr]"><div className="rounded-[20px] border border-[#cfc3b0] bg-[#f4eee3] p-4"><div className="flex items-center justify-between"><div><p className="mono-font text-[9px] uppercase tracking-[.16em] text-[var(--forest-2)]">Preview vùng OCR</p><p className="mt-1 text-sm font-extrabold">douyin_product_review.mp4</p></div><StatusPill label="Preview sẵn sàng" /></div><div className="relative mt-4 aspect-video overflow-hidden rounded-2xl border border-[#264c3e] bg-[#0e1613]"><div className="absolute inset-0 bg-[linear-gradient(135deg,#203c33_0%,#0e1613_46%,#1d2a26_100%)]" /><div className="absolute inset-x-7 top-1/2 h-6 -translate-y-1/2 rounded-full bg-white/10 blur-sm" /><div className="absolute left-[12%] top-[44%] h-[29%] w-[68%] rounded-lg border-2 border-[var(--sun)] bg-[var(--sun)]/10 shadow-[0_0_0_999px_rgba(4,9,7,.46)]"><span className="absolute -top-7 left-2 rounded-md bg-[var(--sun)] px-2 py-1 text-[8px] font-black tracking-[.1em] text-[#35260f]">VÙNG OCR</span><span className="absolute -bottom-2 -right-2 h-4 w-4 rounded-full border-2 border-white bg-[var(--sun)]" /></div><div className="absolute bottom-3 left-3 rounded-md bg-black/35 px-2 py-1 text-[9px] text-white/60">00:21 / 01:08</div></div><div className="mt-3 grid grid-cols-4 gap-2">{[["X","12.0"],["Y","44.0"],["Rộng","68.0"],["Cao","29.0"]].map(([label, value]) => <div key={label} className="rounded-xl border border-[var(--line)] bg-white/65 px-2 py-2"><p className="text-[9px] text-[var(--ink-soft)]">{label} (%)</p><p className="mt-1 mono-font text-xs font-bold">{value}</p></div>)}</div></div><div className="space-y-3"><div className="rounded-[20px] bg-[var(--forest)] p-5 text-white"><div className="flex items-center justify-between"><div><p className="mono-font text-[9px] uppercase tracking-[.16em] text-[var(--mint)]">Hàng đợi video</p><p className="mt-2 display-font text-3xl font-semibold tracking-[-.06em]">03</p></div><QueueIcon size={24} /></div><div className="mt-5 space-y-2 text-[10px]"><div className="flex items-center justify-between rounded-xl bg-white/10 px-3 py-2.5"><span>review_01.mp4</span><span className="text-[var(--mint)]">OCR</span></div><div className="flex items-center justify-between rounded-xl bg-white/10 px-3 py-2.5"><span>review_02.mp4</span><span className="text-white/40">Queued</span></div><div className="flex items-center justify-between rounded-xl bg-white/10 px-3 py-2.5"><span>review_03.mp4</span><span className="text-white/40">Queued</span></div></div></div><div className="rounded-[20px] border border-[#cfc3b0] bg-white/65 p-5"><div className="flex items-center justify-between"><p className="mono-font text-[9px] uppercase tracking-[.16em] text-[var(--ink-soft)]">Tiến trình job</p><StatusPill label="Đang xử lý" tone="pending" /></div><div className="mt-4 grid grid-cols-5 gap-1.5">{["Giải mã","Video","OCR","Dịch","Xong"].map((step, index) => <div key={step} className={`rounded-lg px-1 py-2 text-center text-[8px] font-extrabold ${index === 2 ? "bg-[var(--sun-soft)] text-[#744d14]" : index < 2 ? "bg-[#e5f1e8] text-[#286044]" : "bg-[var(--paper-deep)] text-[var(--ink-soft)]"}`}>{step}</div>)}</div><div className="mt-4 h-2 overflow-hidden rounded-full bg-[#e1dbd0]"><motion.div animate={{ width: ["22%","68%","48%"] }} transition={{ duration: 2.8, repeat: Infinity, ease: "easeInOut" }} className="h-full rounded-full bg-[var(--forest-2)]" /></div><p className="mt-2 text-[10px] text-[var(--ink-soft)]">VideOCR đang tạo raw.srt...</p></div></div></div></div></motion.div>;
}

function FeatureCard({ item }: { item: (typeof featureItems)[number] }) {
  const Icon = item.icon;
  const tone = item.accent === "sun" ? "bg-[var(--sun-soft)] text-[#8b631f]" : item.accent === "forest" ? "bg-[var(--forest)] text-white" : item.accent === "blue" ? "bg-[#e6f0f4] text-[var(--blue)]" : "bg-[#e5f1e8] text-[#286044]";
  return <motion.article variants={reveal} initial="hidden" whileInView="visible" viewport={{ once: true, amount: 0.2 }} whileHover={{ y: -6 }} className="group flex min-h-[290px] flex-col justify-between rounded-[23px] border border-[var(--line)] bg-[rgba(255,250,240,.84)] p-7 shadow-[var(--shadow-card)] transition-shadow hover:shadow-[var(--shadow-soft)]"><div><div className={`mb-12 flex h-12 w-12 items-center justify-center rounded-2xl ${tone}`}><Icon size={21} /></div><p className="mono-font text-[10px] font-bold tracking-[.15em] text-[var(--ink-soft)]">{item.number}</p><h3 className="display-font mt-3 text-2xl font-semibold tracking-[-.05em]">{item.title}</h3><p className="mt-3 text-sm leading-7 text-[var(--ink-soft)]">{item.text}</p></div><div className="mt-8 flex items-center justify-between border-t border-[var(--line)] pt-4 text-xs font-extrabold text-[var(--forest)]"><span>Đọc chi tiết trong docs</span><ArrowUpRight size={15} className="transition-transform group-hover:translate-x-1 group-hover:-translate-y-1" /></div></motion.article>;
}

function CodeBlock() {
  const [copied, setCopied] = useState(false);
  const code = `# Kiểm tra output sau khi dịch\nproject/\n├── product-review.mp4\n├── product-review.raw.srt\n├── product-review.vi.srt\n└── project.json`;
  const copy = async () => { await navigator.clipboard?.writeText(code); setCopied(true); window.setTimeout(() => setCopied(false), 1400); };
  return <div className="relative overflow-hidden rounded-2xl border border-[#294a3e] bg-[#11231e] p-5 text-sm leading-7 text-[#c9d9d1] shadow-[0_16px_40px_rgba(23,63,53,.16)]"><button aria-label="Copy output example" onClick={copy} className="focus-ring absolute right-4 top-4 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1.5 text-[11px] font-bold text-white/60 transition hover:bg-white/10 hover:text-white">{copied ? <Check size={13} /> : <Copy size={13} />}{copied ? "Đã copy" : "Copy"}</button><pre className="overflow-x-auto pr-20"><code><span className="text-[#f1c878]"># Kiểm tra output sau khi dịch</span>{"\n"}<span className="text-[#9ed3af]">project/</span>{"\n├── product-review.mp4\n├── product-review.raw.srt\n├── product-review.vi.srt\n└── project.json"}</code></pre></div>;
}

function DocsSection({ id, label, title, children }: { id: string; label: string; title: string; children: React.ReactNode }) {
  return <article id={id} className="scroll-mt-28 border-b border-[var(--line)] pb-14 pt-2 last:border-0"><p className="mono-font mb-3 text-[10px] font-bold uppercase tracking-[.16em] text-[var(--forest-2)]">{label}</p><h3 className="display-font text-3xl font-semibold tracking-[-.05em]">{title}</h3><div className="mt-5 space-y-5 text-[15px] leading-8 text-[var(--ink-soft)]">{children}</div></article>;
}

function SystemCard() {
  return <div className="rounded-[26px] border border-[#c7bca9] bg-[var(--paper)] p-7 shadow-[var(--shadow-card)]"><div className="flex items-center justify-between"><div><p className="section-kicker">Bản portable</p><h3 className="display-font mt-3 text-3xl font-semibold tracking-[-.05em]">VietSub Studio v1.5.3</h3><p className="mt-2 text-sm text-[var(--ink-soft)]">Windows-first, mã nguồn mở, cài nhanh từ GitHub Releases.</p></div><div className="hidden h-14 w-14 overflow-hidden rounded-2xl border-2 border-white shadow-[5px_5px_0_var(--sun)] sm:block"><img src="/avatar.jpg" alt="VietSub Studio" className="h-full w-full object-cover" /></div></div><div className="mt-7 grid gap-3 sm:grid-cols-2"><div className="rounded-2xl bg-[var(--paper-deep)] p-4"><p className="mono-font text-[9px] uppercase tracking-[.14em] text-[var(--ink-soft)]">Bắt buộc</p><ul className="mt-3 space-y-2 text-xs font-bold text-ink/75"><li className="flex gap-2"><Check size={14} className="text-[var(--forest-2)]" /> Windows 10/11 x64</li><li className="flex gap-2"><Check size={14} className="text-[var(--forest-2)]" /> Python 3.12+ nếu chạy source</li><li className="flex gap-2"><Check size={14} className="text-[var(--forest-2)]" /> VideOCR CLI cho OCR</li></ul></div><div className="rounded-2xl bg-[#edf3f5] p-4"><p className="mono-font text-[9px] uppercase tracking-[.14em] text-[var(--blue)]">Khuyến nghị</p><ul className="mt-3 space-y-2 text-xs font-bold text-ink/75"><li className="flex gap-2"><Check size={14} className="text-[var(--blue)]" /> FFmpeg + FFprobe</li><li className="flex gap-2"><Check size={14} className="text-[var(--blue)]" /> Douzy nếu dùng link Douyin</li><li className="flex gap-2"><Check size={14} className="text-[var(--blue)]" /> Edge cho Gemini Notebook</li></ul></div></div><ButtonLink href={releaseUrl} external variant="dark">Tải bản portable mới nhất</ButtonLink></div>;
}

function RelaySite() {
  const [menuOpen, setMenuOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const [activeDoc, setActiveDoc] = useState("quickstart");
  const [openFaq, setOpenFaq] = useState<number | null>(0);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 20);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    const observer = new IntersectionObserver((entries) => {
      const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio);
      if (visible[0]) setActiveDoc(visible[0].target.id);
    }, { rootMargin: "-18% 0px -65% 0px", threshold: [0.1, 0.45, 0.8] });
    docs.forEach(({ id }) => { const node = document.getElementById(id); if (node) observer.observe(node); });
    return () => observer.disconnect();
  }, []);

  const activeLabel = useMemo(() => docs.find((item) => item.id === activeDoc)?.label ?? "Bắt đầu nhanh", [activeDoc]);

  return <main className="site-shell">
    <header className={`fixed inset-x-0 top-0 z-50 transition-all duration-300 ${scrolled ? "border-b border-[var(--line)] bg-[rgba(255,250,240,.84)] shadow-[0_10px_35px_rgba(31,43,37,.07)] backdrop-blur-xl" : "bg-transparent"}`}>
      <div className="mx-auto flex max-w-7xl items-center justify-between px-5 py-4 sm:px-8 lg:px-10"><a href="#top" className="focus-ring display-font flex items-center gap-3 text-lg font-semibold tracking-[-.06em]"><span className="h-8 w-8 overflow-hidden rounded-[10px] border-2 border-white shadow-[3px_3px_0_var(--sun)]"><img src="/avatar.jpg" alt="" className="h-full w-full object-cover" /></span><span>VietSub Studio</span></a><nav className="hidden items-center gap-8 text-sm font-bold text-[var(--ink-soft)] lg:flex">{navItems.map((item) => <a href={item.href} key={item.href} className="focus-ring transition hover:text-[var(--forest)]">{item.label}</a>)}</nav><div className="hidden items-center gap-3 lg:flex"><a href={repoUrl} target="_blank" rel="noreferrer" className="focus-ring inline-flex items-center gap-2 px-3 py-2 text-sm font-bold text-[var(--ink-soft)] transition hover:text-[var(--forest)]"><Github size={16} /> GitHub</a><ButtonLink href={releaseUrl} external>Tải portable</ButtonLink></div><button aria-label={menuOpen ? "Đóng menu" : "Mở menu"} aria-expanded={menuOpen} onClick={() => setMenuOpen((value) => !value)} className="focus-ring inline-flex h-10 w-10 items-center justify-center rounded-full border border-[var(--line)] bg-white/65 lg:hidden">{menuOpen ? <X size={18} /> : <Menu size={18} />}</button></div>
      <AnimatePresence>{menuOpen && <motion.nav initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="border-t border-[var(--line)] bg-[var(--paper)] px-5 py-4 lg:hidden"><div className="mx-auto flex max-w-7xl flex-col gap-1">{navItems.map((item) => <a href={item.href} key={item.href} onClick={() => setMenuOpen(false)} className="rounded-xl px-3 py-3 text-sm font-bold text-[var(--ink-soft)] hover:bg-white hover:text-[var(--forest)]">{item.label}</a>)}<a href={releaseUrl} target="_blank" rel="noreferrer" onClick={() => setMenuOpen(false)} className="mt-2 inline-flex items-center justify-between rounded-xl bg-[var(--forest)] px-4 py-3 text-sm font-bold text-white">Tải bản portable <Download size={16} /></a></div></motion.nav>}</AnimatePresence>
    </header>

    <section id="top" className="relative grid-paper pt-32 sm:pt-40"><div className="noise absolute inset-0" /><div className="mx-auto grid max-w-7xl gap-14 px-5 pb-20 sm:px-8 sm:pb-28 lg:grid-cols-[.87fr_1.13fr] lg:items-center lg:gap-12 lg:px-10 lg:pb-36"><motion.div variants={reveal} initial="hidden" animate="visible" className="relative z-10 max-w-2xl"><p className="section-kicker mb-6">OCR phụ đề cháy trên video</p><h1 className="hero-title display-font text-[clamp(4rem,8.5vw,8.2rem)] font-semibold leading-[.84] tracking-[-.09em] text-ink">Từ video<br /><span className="text-[var(--forest-2)]">đến SRT.</span></h1><p className="mt-8 max-w-xl text-lg leading-8 text-[var(--ink-soft)] sm:text-xl">VietSub Studio điều phối trọn workflow: nhận Douyin hoặc MP4, crop đúng vùng chữ, chạy VideOCR, dịch sang tiếng Việt và giữ nguyên timestamp trước khi lưu project.</p><div className="hero-actions mt-9 flex flex-wrap items-center gap-4"><ButtonLink href={releaseUrl} external>Tải bản portable</ButtonLink><a href="#workflow" className="focus-ring group inline-flex items-center gap-2 rounded-xl px-3 py-3 text-sm font-extrabold text-[var(--forest)]"><Play size={17} className="text-[var(--sun)]" /> Xem quy trình <ArrowRight size={16} className="transition-transform group-hover:translate-x-1" /></a></div><div className="mt-10 flex flex-wrap items-center gap-x-6 gap-y-3 text-xs font-bold text-[var(--ink-soft)]"><span className="flex items-center gap-2"><Check size={14} className="text-[var(--forest-2)]" /> Windows 10/11</span><span className="flex items-center gap-2"><Check size={14} className="text-[var(--forest-2)]" /> MIT license</span><span className="flex items-center gap-2"><Check size={14} className="text-[var(--forest-2)]" /> OpenAI-compatible</span></div></motion.div><StudioPreview /></div><div className="mx-auto flex max-w-7xl flex-wrap items-center gap-3 border-t border-[var(--line)] px-5 py-6 text-xs font-bold text-[var(--ink-soft)] sm:px-8 lg:px-10"><span className="mono-font mr-4 uppercase tracking-[.15em]">Pipeline đã dùng</span><StatusPill label="Douyin / MP4" /><StatusPill label="VideOCR" /><StatusPill label="Gemini Notebook" tone="pending" /><StatusPill label="DeepSeek / OpenRouter" /><StatusPill label="SRT validation" /></div></section>

    <section id="features" className="section-pad mx-auto max-w-7xl px-5 py-28 sm:px-8 lg:px-10 lg:py-40"><SectionHeading eyebrow="Tính năng cốt lõi" title="Một workflow nhỏ, nhưng không bỏ sót bước quan trọng." body="Studio không cố thay thế mọi tool. Nó giữ phần điều phối để OCR, dịch, validation và file output không bị rời rạc." /><div className="mt-16 grid gap-4 md:grid-cols-2">{featureItems.map((item) => <FeatureCard item={item} key={item.title} />)}</div></section>

    <section id="workflow" className="section-pad border-y border-[var(--line)] bg-[rgba(247,240,227,.72)] py-28 sm:py-36"><div className="mx-auto max-w-7xl px-5 sm:px-8 lg:px-10"><SectionHeading eyebrow="Quy trình xử lý" title="Từ nguồn video đến project tự chứa." body="Mỗi bước có trạng thái riêng, có thể retry ở đúng điểm lỗi và chỉ coi là thành công sau khi file cuối cùng vượt validation." /><div className="mt-16 grid gap-10 lg:grid-cols-[.8fr_1.2fr] lg:gap-20"><div className="space-y-4">{workflowSteps.map((step, index) => { const Icon = step.icon; return <motion.div key={step.number} variants={reveal} initial="hidden" whileInView="visible" viewport={{ once: true, amount: .25 }} transition={{ delay: index * .08 }} className="group border-t border-[var(--line)] py-7"><div className="flex gap-5"><span className="mono-font pt-1 text-xs font-bold text-[var(--forest-2)]">{step.number}</span><div><h3 className="display-font flex items-center gap-3 text-2xl font-semibold tracking-[-.04em]"><Icon size={20} className="text-[var(--sun)]" />{step.title}</h3><p className="mt-2 max-w-md text-sm leading-7 text-[var(--ink-soft)]">{step.text}</p></div><ArrowUpRight size={18} className="ml-auto text-[var(--ink-soft)] opacity-0 transition-all group-hover:translate-x-1 group-hover:-translate-y-1 group-hover:opacity-100" /></div></motion.div>; })}</div><motion.div variants={reveal} initial="hidden" whileInView="visible" viewport={{ once: true, amount: .2 }} className="relative overflow-hidden rounded-[30px] bg-[var(--forest)] p-7 text-white shadow-[var(--shadow-soft)] sm:p-10"><div className="absolute -right-20 -top-24 h-72 w-72 rounded-full bg-[var(--sun)]/20 blur-3xl" /><div className="absolute -bottom-28 -left-16 h-72 w-72 rounded-full bg-[var(--mint)]/15 blur-3xl" /><div className="relative"><div className="flex items-center justify-between border-b border-white/10 pb-5"><span className="mono-font text-[10px] uppercase tracking-[.15em] text-white/45">workflow state</span><span className="flex items-center gap-2 text-xs text-[var(--mint)]"><span className="h-2 w-2 animate-pulse rounded-full bg-[var(--mint)]" /> worker ready</span></div><div className="mt-10 rounded-2xl border border-white/10 bg-white/[.06] p-5"><div className="flex items-center justify-between text-xs"><span className="flex items-center gap-2 font-bold"><FileText size={15} className="text-[var(--sun)]" /> product-review.raw.srt</span><span className="text-white/45">OCR source</span></div><div className="mt-5 space-y-3 text-sm text-white/70"><p><span className="mr-3 text-[10px] text-[var(--sun)]">00:00:14</span>大家好, hôm nay chúng ta kiểm tra sản phẩm này.</p><p className="rounded-xl border-l-2 border-[var(--sun)] bg-white/[.05] px-3 py-2 text-white">Xin chào, hôm nay chúng ta sẽ kiểm tra sản phẩm này.</p></div><div className="mt-5 flex items-center justify-between border-t border-white/10 pt-4 text-[10px] text-white/45"><span>12 blocks</span><span className="flex items-center gap-1 text-[var(--mint)]"><CircleCheck size={13} /> timestamp giữ nguyên</span></div></div><div className="my-6 flex items-center gap-3 text-xs text-white/45"><span className="h-px flex-1 bg-white/10" /><ArrowDown size={14} /><span className="h-px flex-1 bg-white/10" /></div><div className="grid gap-3 sm:grid-cols-3"><div className="rounded-2xl border border-white/10 bg-white/[.06] p-4"><div className="text-2xl font-semibold">raw</div><p className="mt-1 text-[10px] text-white/45">OCR subtitle</p></div><div className="rounded-2xl border border-white/10 bg-white/[.06] p-4"><div className="text-2xl font-semibold">vi</div><p className="mt-1 text-[10px] text-white/45">translated</p></div><div className="rounded-2xl border border-white/10 bg-white/[.06] p-4"><div className="text-2xl font-semibold">OK</div><p className="mt-1 text-[10px] text-white/45">validated</p></div></div></div></motion.div></div></div></section>

    <section id="docs" className="section-pad mx-auto max-w-7xl px-5 py-28 sm:px-8 lg:px-10 lg:py-40"><div className="mb-16 flex flex-col justify-between gap-6 border-b border-[var(--line)] pb-8 sm:flex-row sm:items-end"><SectionHeading eyebrow="Docs / workflow" title="Không cần đoán tool nào chạy trước." body="Tài liệu ngắn để bạn cài phụ thuộc, chọn provider và hiểu output mà Studio tạo ra." /><div className="mono-font text-right text-[10px] font-bold uppercase tracking-[.14em] text-[var(--ink-soft)]">Đang xem<br /><span className="mt-1 inline-block text-[var(--forest)]">{activeLabel}</span></div></div><div className="docs-grid grid grid-cols-[220px_1fr] gap-10 lg:gap-20"><aside className="docs-aside sticky top-28 h-fit"><p className="mono-font mb-4 text-[10px] font-bold uppercase tracking-[.15em] text-[var(--ink-soft)]">Mục lục</p><nav className="space-y-1">{docs.map((item) => <a href={`#${item.id}`} key={item.id} className={`focus-ring group flex items-center justify-between rounded-xl px-3 py-2.5 text-sm font-bold transition ${activeDoc === item.id ? "bg-[var(--forest)] text-white" : "text-[var(--ink-soft)] hover:bg-white hover:text-[var(--forest)]"}`}><span>{item.label}</span><ChevronRight size={15} className={activeDoc === item.id ? "text-[var(--sun)]" : "opacity-0 transition group-hover:opacity-100"} /></a>)}</nav><div className="mt-10 rounded-2xl border border-[var(--line)] bg-white/55 p-4"><div className="flex items-center gap-2 text-xs font-extrabold"><Github size={14} className="text-[var(--forest-2)]" /> Open source</div><p className="mt-2 text-xs leading-5 text-[var(--ink-soft)]">Bug report, release notes và source code đều nằm trên GitHub repository.</p><a href={repoUrl} target="_blank" rel="noreferrer" className="mt-4 inline-flex items-center gap-1 text-xs font-extrabold text-[var(--forest)]">Mở repository <ArrowUpRight size={13} /></a></div></aside><div className="max-w-3xl space-y-12"><DocsSection id="quickstart" label="01 / bắt đầu nhanh" title="Cài bản portable rồi mở Studio"><p>Tải asset `VietSub-Studio-Portable-v*.zip` từ GitHub Releases, giải nén vào thư mục có quyền ghi và mở `VietSub Studio.exe`. Portable chỉ đóng gói ứng dụng điều phối; Douzy, VideOCR, FFmpeg, Edge và tài khoản AI vẫn là phụ thuộc bên ngoài.</p><div className="grid gap-3 sm:grid-cols-3"><div className="rounded-2xl border border-[var(--line)] bg-white/60 p-4"><Download size={17} className="text-[var(--forest-2)]" /><p className="mt-8 text-sm font-extrabold">1. Tải app</p><p className="mt-1 text-xs leading-5 text-[var(--ink-soft)]">Windows 10/11 x64.</p></div><div className="rounded-2xl border border-[var(--line)] bg-white/60 p-4"><Settings2 size={17} className="text-[#8b631f]" /><p className="mt-8 text-sm font-extrabold">2. Cài tool</p><p className="mt-1 text-xs leading-5 text-[var(--ink-soft)]">VideOCR và provider bạn dùng.</p></div><div className="rounded-2xl border border-[var(--line)] bg-white/60 p-4"><Play size={17} className="text-[var(--blue)]" /><p className="mt-8 text-sm font-extrabold">3. Chạy flow</p><p className="mt-1 text-xs leading-5 text-[var(--ink-soft)]">Preview to queue to run.</p></div></div></DocsSection><DocsSection id="input" label="02 / nguồn và OCR" title="Preview trước khi để OCR chạy"><p>Dán URL Douyin đầy đủ, link ngắn `v.douyin.com` hoặc chọn MP4 local. Studio hiển thị video preview, cho phép kéo crop theo phần trăm và lưu crop mặc định. OCR hỗ trợ `.ch`, `.en`, `.ja`, `.ko`, `.vi` cùng các mode `auto`, `fast`, `accurate`.</p><div className="rounded-2xl border border-[var(--sun)]/40 bg-[var(--sun-soft)] p-5 text-sm leading-7 text-ink"><strong className="flex items-center gap-2"><CropIcon size={16} /> Tip</strong><span className="mt-2 block text-ink/70">Nếu video dài hơn 45 phút, Studio tách các chunk OCR 10 phút rồi merge lại theo timestamp gốc.</span></div></DocsSection><DocsSection id="providers" label="03 / provider dịch" title="Chọn cách dịch phù hợp với setup"><p>Gemini Notebook chạy qua Microsoft Edge với profile debug riêng. Nếu muốn API, chọn DeepSeek, OpenRouter hoặc Custom rồi nhập Base URL, model và API key. API có streaming khi gateway hỗ trợ, tự fallback JSON và retry tối đa 3 lần cho lỗi tạm thời.</p><div className="grid gap-3 sm:grid-cols-2"><div className="rounded-2xl border border-[var(--line)] bg-white/60 p-4"><Languages size={17} className="text-[var(--forest-2)]" /><p className="mt-6 text-sm font-extrabold">Notebook</p><p className="mt-1 text-xs leading-5 text-[var(--ink-soft)]">Gemini qua Edge / Playwright.</p></div><div className="rounded-2xl border border-[var(--line)] bg-white/60 p-4"><Code2 size={17} className="text-[var(--blue)]" /><p className="mt-6 text-sm font-extrabold">API compatible</p><p className="mt-1 text-xs leading-5 text-[var(--ink-soft)]">DeepSeek, OpenRouter, custom gateway.</p></div></div></DocsSection><DocsSection id="queue" label="04 / queue và retry" title="Lỗi ở đâu, retry đúng từ đó"><p>Job có trạng thái `.queued`, `.running`, `.success`, `.error`, `.cancelled`. Worker xử lý tuần tự, giữ log và output trung gian, nên lỗi ở bước dịch không buộc bạn download và OCR lại từ đầu.</p><div className="rounded-2xl border border-[var(--line)] bg-white/60 p-5"><div className="flex flex-wrap items-center gap-2 text-xs font-extrabold"><span className="rounded-full bg-[#e5f1e8] px-3 py-1 text-[#286044]">OCR success</span><ArrowRight size={14} className="text-[var(--ink-soft)]" /><span className="rounded-full bg-[#fff0ed] px-3 py-1 text-[var(--coral)]">Translate error</span><ArrowRight size={14} className="text-[var(--ink-soft)]" /><span className="rounded-full bg-[var(--sun-soft)] px-3 py-1 text-[#744d14]">Retry translate</span></div></div></DocsSection><DocsSection id="output" label="05 / output và privacy" title="Một folder cho mỗi project"><p>Sau validation, Studio lưu video, thumbnail nếu có, `.raw.srt`, `.vi.srt`, asset Douzy và `.project.json` vào một folder riêng. App chỉ bind Flask trên `127.0.0.1`, lưu config local và cảnh báo nếu output nằm trong OneDrive.</p><CodeBlock /><div className="rounded-2xl border border-[var(--forest-2)]/25 bg-[#e5f1e8] p-5 text-sm leading-7 text-ink"><strong className="flex items-center gap-2"><ShieldCheck size={16} className="text-[var(--forest-2)]" /> Rõ ràng về dữ liệu</strong><span className="mt-2 block text-ink/70">VietSub Studio không phải provider AI và không kèm tài khoản Google/API. Bạn tự chọn dịch vụ, tự quản lý credential và output local.</span></div></DocsSection></div></div></section>

    <section id="system" className="section-pad border-y border-[var(--line)] bg-[var(--forest)] py-28 text-white sm:py-36"><div className="mx-auto grid max-w-7xl gap-12 px-5 sm:px-8 lg:grid-cols-[.8fr_1.2fr] lg:items-center lg:gap-20 lg:px-10"><div><SectionHeading inverse eyebrow="Tải và triển khai" title="Không có pricing wall. Chỉ có workflow rõ ràng." body="VietSub Studio là dự án mã nguồn mở MIT. Tải bản portable, cài những phụ thuộc bạn cần và bắt đầu từ một video." /><div className="mt-8 flex flex-wrap gap-3"><ButtonLink href={releaseUrl} external variant="primary">Tải release mới nhất</ButtonLink><ButtonLink href={repoUrl} external variant="secondary">Xem source trên GitHub</ButtonLink></div></div><SystemCard /></div></section>

    <section id="faq" className="section-pad mx-auto max-w-7xl px-5 py-28 sm:px-8 lg:px-10 lg:py-36"><div className="grid gap-12 lg:grid-cols-[.7fr_1.3fr] lg:gap-24"><SectionHeading eyebrow="FAQ" title="Những câu hỏi trước khi chạy job đầu tiên." body="Nếu còn vướng, mở README hoặc tạo issue trên GitHub để cùng debug." /><div className="divide-y divide-[var(--line)] border-y border-[var(--line)]">{faqs.map((faq, index) => { const open = openFaq === index; return <div key={faq.q}><button aria-expanded={open} onClick={() => setOpenFaq(open ? null : index)} className="focus-ring flex w-full items-center justify-between gap-5 py-6 text-left"><span className="display-font text-xl font-semibold tracking-[-.03em]">{faq.q}</span><span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-[var(--line)] transition ${open ? "bg-[var(--forest)] text-white" : "bg-white/55"}`}>{open ? <Minus size={15} /> : <Plus size={15} />}</span></button><AnimatePresence initial={false}>{open && <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden"><p className="max-w-2xl pb-6 pr-14 text-sm leading-7 text-[var(--ink-soft)]">{faq.a}</p></motion.div>}</AnimatePresence></div>; })}</div></div></section>

    <section className="px-5 pb-8 sm:px-8 lg:px-10"><div className="relative mx-auto max-w-7xl overflow-hidden rounded-[30px] bg-[var(--sun)] px-7 py-14 text-[#35260f] sm:px-12 sm:py-16"><div className="absolute -right-24 -top-40 h-96 w-96 rounded-full border-[70px] border-white/20" /><div className="relative flex flex-col justify-between gap-10 lg:flex-row lg:items-end"><div className="max-w-2xl"><p className="mono-font text-[10px] font-bold uppercase tracking-[.17em] text-[#744d14]">Bắt đầu từ video kế tiếp</p><h2 className="display-font mt-5 text-4xl font-semibold tracking-[-.06em] sm:text-6xl">OCR đúng vùng. Dịch đúng block. Lưu đúng project.</h2></div><ButtonLink href={releaseUrl} external variant="dark">Tải VietSub Studio</ButtonLink></div></div></section>

    <footer className="mx-auto max-w-7xl px-5 pb-10 pt-16 sm:px-8 lg:px-10"><div className="grid gap-10 border-b border-[var(--line)] pb-12 sm:grid-cols-2 lg:grid-cols-[1.5fr_repeat(3,1fr)]"><div><a href="#top" className="display-font flex items-center gap-3 text-lg font-semibold tracking-[-.06em]"><span className="h-8 w-8 overflow-hidden rounded-[10px] border-2 border-white shadow-[3px_3px_0_var(--sun)]"><img src="/avatar.jpg" alt="" className="h-full w-full object-cover" /></span>VietSub Studio</a><p className="mt-4 max-w-xs text-sm leading-6 text-[var(--ink-soft)]">Lớp điều phối mã nguồn mở cho workflow OCR phụ đề và dịch tiếng Việt trên Windows.</p></div><div><p className="mono-font text-[10px] font-bold uppercase tracking-[.15em] text-[var(--ink-soft)]">Đi tới</p><div className="mt-4 space-y-3 text-sm font-extrabold text-ink/70"><a href="#features" className="block hover:text-[var(--forest)]">Tính năng</a><a href="#workflow" className="block hover:text-[var(--forest)]">Quy trình</a><a href="#docs" className="block hover:text-[var(--forest)]">Docs</a></div></div><div><p className="mono-font text-[10px] font-bold uppercase tracking-[.15em] text-[var(--ink-soft)]">Project</p><div className="mt-4 space-y-3 text-sm font-extrabold text-ink/70"><a href={repoUrl} target="_blank" rel="noreferrer" className="block hover:text-[var(--forest)]">GitHub</a><a href={releaseUrl} target="_blank" rel="noreferrer" className="block hover:text-[var(--forest)]">Releases</a><a href="#faq" className="block hover:text-[var(--forest)]">FAQ</a></div></div><div><p className="mono-font text-[10px] font-bold uppercase tracking-[.15em] text-[var(--ink-soft)]">Stack</p><div className="mt-4 space-y-3 text-sm font-extrabold text-ink/70"><span className="block">Python + Flask</span><span className="block">VideOCR + FFmpeg</span><span className="block">MIT License</span></div></div></div><div className="flex flex-col justify-between gap-3 pt-6 text-xs text-[var(--ink-soft)] sm:flex-row"><span>(c) 2026 VietSub Studio. Open source workflow, Windows-first.</span><span className="mono-font uppercase tracking-[.12em]">v1.5.3 / made for subtitle work</span></div></footer>
  </main>;
}

export default RelaySite;
