"use client";

import { AppWindow, Gauge, HardDrive, Languages, Sparkles, Zap } from "lucide-react";
import { motion } from "motion/react";
import type { Variants } from "motion/react";
import { useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { VERSION } from "./data";

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
          <Zap size={15} aria-hidden="true" /> Bản v{VERSION}
        </span>
        <h3>Nhiều link, nhiều video. Một hàng đợi.</h3>
        <p>
          Dán mỗi link một dòng hoặc chọn nhiều MP4/MKV để thêm tuần tự. Mỗi video giữ tên riêng, dùng crop mặc định;
          video nào chưa thêm được sẽ có thông báo riêng. Tải nhiều kết nối và preview từng video vẫn có sẵn.
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
            <dt>Benchmark tải 6 MB của v1.6.2</dt>
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
        <h3>Chrome chạy nền, hiện lên khi bạn cần</h3>
        <p>
          Mặc định headless, dùng profile riêng và tái sử dụng tab đầu tiên. Chrome của app tự đóng sau 60 giây nhàn rỗi.
          Gặp captcha, cùng profile mở lại để bạn xác minh; lượt tự động sau trở về chạy nền.
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
            <small>Headless · hiện khi xác minh</small>
          </div>
        </div>
      </Card>

      <Card className="bento-c">
        <span className="bento-icon">
          <HardDrive size={22} aria-hidden="true" />
        </span>
        <h3>Dọn hàng đợi, giữ lại phụ đề</h3>
        <p>
          Chọn job đã kết thúc hoặc quá 3 ngày, xem dung lượng có thể dọn rồi chọn xóa bản ghi hay video/file tạm.
          SRT được giữ mặc định. Có thể bật tự dọn nguồn sau khi xuất sub thành công.
        </p>
      </Card>

      <Card className="bento-d">
        <span className="bento-icon">
          <Sparkles size={22} aria-hidden="true" />
        </span>
        <h3>Thêm tiêu đề, mô tả và hashtag</h3>
        <p>Bật publish pack để AI tạo thêm <code>.publish.json</code> cạnh phụ đề với nội dung tiếng Việt.
          Bạn duyệt trước khi đăng; tính năng mặc định tắt và không tự đăng video.</p>
      </Card>

      <Card className="bento-e">
        <span className="bento-icon">
          <Languages size={22} aria-hidden="true" />
        </span>
        <h3>Giữ đúng từng timestamp</h3>
        <p>
          Dịch bằng DeepSeek, OpenRouter hoặc Gemini Notebook. Index, số block và timecode được đối chiếu với SRT gốc
          trước khi lưu. Workbench vẫn giữ preview, crop và nhật ký trong cùng cửa sổ.
        </p>
      </Card>
    </motion.div>
  );
}
