"use client";

import { AppWindow, FolderOpen, Gauge, Languages, RefreshCw, ShieldCheck, Zap } from "lucide-react";
import { motion } from "motion/react";
import type { Variants } from "motion/react";
import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

const grid: Variants = { hidden: {}, show: { transition: { staggerChildren: 0.09, delayChildren: 0.05 } } };
const card: Variants = {
  hidden: { opacity: 0, y: 26 },
  show: { opacity: 1, y: 0, transition: { duration: 0.55, ease: [0.22, 1, 0.36, 1] } },
};

function Card({ className, children }: { className: string; children: ReactNode }) {
  return (
    <motion.article className={`bento-card ${className}`} variants={card}>
      <div className="bento-inner">{children}</div>
    </motion.article>
  );
}

const SOURCES = [
  { han: "抖音", label: "Douyin", note: "bản gốc không watermark" },
  { han: "哔哩哔哩", label: "Bilibili", note: "tới 1080p H.264" },
  { han: "小红书", label: "Xiaohongshu", note: "từ link xhslink" },
  { han: "快手", label: "Kuaishou", note: "từ đoạn share" },
];

/** A border light that follows the cursor across every card. Coordinates are written per card, at most once a frame. */
export function BentoGrid() {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el || !matchMedia("(hover: hover) and (pointer: fine)").matches) return;
    let frame = 0;
    let x = 0;
    let y = 0;
    const paint = () => {
      frame = 0;
      for (const c of el.querySelectorAll<HTMLElement>(".bento-card")) {
        const r = c.getBoundingClientRect();
        c.style.setProperty("--mx", `${x - r.left}px`);
        c.style.setProperty("--my", `${y - r.top}px`);
      }
    };
    const onMove = (event: PointerEvent) => {
      x = event.clientX;
      y = event.clientY;
      if (!frame) frame = requestAnimationFrame(paint);
    };
    const onEnter = () => el.classList.add("is-lit");
    const onLeave = () => el.classList.remove("is-lit");
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerenter", onEnter);
    el.addEventListener("pointerleave", onLeave);
    return () => {
      cancelAnimationFrame(frame);
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerenter", onEnter);
      el.removeEventListener("pointerleave", onLeave);
    };
  }, []);

  return (
    <motion.div
      ref={ref}
      className="bento"
      variants={grid}
      initial="hidden"
      whileInView="show"
      viewport={{ once: true, amount: 0.15 }}
    >
      <Card className="bento-a">
        <span className="bento-kicker">
          <Zap size={15} aria-hidden="true" /> Mới trong v1.6.2
        </span>
        <h3>Dán nguyên đoạn share, nhận video gốc. Tải 6 kết nối song song.</h3>
        <p>
          App tự tìm link trong đoạn chữ chia sẻ, lấy bản gốc rồi tải theo 6 đoạn HTTP Range cùng lúc. Server không hỗ trợ Range thì
          tự quay về tải một luồng; đoạn nào đứt thì tải tiếp từ chỗ dừng.
        </p>
        <ul className="source-list">
          {SOURCES.map((s) => (
            <li key={s.label}>
              <span lang="zh-CN" className="source-han">
                {s.han}
              </span>
              <span className="source-name">{s.label}</span>
              <span className="source-note">{s.note}</span>
            </li>
          ))}
        </ul>
        <div className="range-viz" aria-hidden="true">
          {Array.from({ length: 6 }, (_, i) => (
            <span key={i} style={{ animationDelay: `${i * 0.12}s` }}>
              <em style={{ animationDelay: `${i * 0.12}s` }} />
            </span>
          ))}
        </div>
        <dl className="bento-stats">
          <div>
            <dt>6 MB, máy chủ giới hạn mỗi kết nối</dt>
            <dd>15,4 s → 2,6 s</dd>
          </div>
          <div>
            <dt>
              <Gauge size={14} aria-hidden="true" /> Bitrate cho OCR
            </dt>
            <dd>H.264 1,5–3,5 Mbps</dd>
          </div>
        </dl>
      </Card>

      <Card className="bento-b">
        <span className="bento-icon">
          <AppWindow size={22} aria-hidden="true" />
        </span>
        <h3>Chrome riêng, không đụng trình duyệt của bạn</h3>
        <p>
          App mở Google Chrome với <code>--user-data-dir</code> trong thư mục cấu hình. Chrome hằng ngày của bạn vẫn chạy song song, cookie và
          extension không bị đọc hay sửa.
        </p>
        <div className="profiles" aria-hidden="true">
          <div className="profile-win">
            <span className="profile-dots" />
            <strong>Chrome của bạn</strong>
            <small>Default profile · giữ nguyên</small>
          </div>
          <div className="profile-win is-app">
            <span className="profile-dots" />
            <strong>VietSub profile</strong>
            <small>--user-data-dir · chỉ mở khi cần</small>
          </div>
        </div>
      </Card>

      <Card className="bento-c">
        <span className="bento-icon">
          <FolderOpen size={22} aria-hidden="true" />
        </span>
        <h3>File MP4/MKV dùng tại chỗ</h3>
        <p>
          Không upload, không sao chép qua <code>%TEMP%</code>. Cùng ổ đĩa thì app hard-link file vào project, nên video vài GB cũng mở ngay.
        </p>
      </Card>

      <Card className="bento-d">
        <span className="bento-icon">
          <Languages size={22} aria-hidden="true" />
        </span>
        <h3>DeepSeek API hoặc Gemini Notebook</h3>
        <p>Dịch theo đoạn, đối chiếu index và timestamp với SRT gốc. Block thiếu hoặc dính nhau bị chặn trước khi lưu.</p>
      </Card>

      <Card className="bento-e">
        <span className="bento-icon">
          <RefreshCw size={22} aria-hidden="true" />
        </span>
        <h3>Tự cập nhật qua GitHub Releases</h3>
        <p>
          App kiểm tra bản mới, tải bản portable và so <ShieldCheck size={14} aria-hidden="true" className="inline-icon" /> SHA256 trước khi
          thay file.
        </p>
      </Card>
    </motion.div>
  );
}
