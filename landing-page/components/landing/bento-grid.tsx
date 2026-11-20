"use client";

import {
  AudioLines,
  Check,
  Clapperboard,
  Cpu,
  FileCheck2,
  HardDrive,
  Layers3,
  LockKeyhole,
  ScanText,
  Sparkles,
  Zap,
} from "lucide-react";
import { motion } from "motion/react";
import { useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { usePrefersReducedMotion } from "./use-reduced-motion";

function Card({
  className,
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  const reduced = usePrefersReducedMotion();
  return (
    <motion.article
      className={`bento-card ${className ?? ""}`}
      whileHover={reduced ? undefined : { y: -5, rotateX: 0.5, rotateY: -0.5 }}
      transition={{ type: "spring", stiffness: 170, damping: 23 }}
    >
      <div className="bento-inner">{children}</div>
    </motion.article>
  );
}

export function BentoGrid() {
  const ref = useRef<HTMLDivElement>(null);
  const reduced = usePrefersReducedMotion();
  useEffect(() => {
    const el = ref.current;
    if (
      !el ||
      reduced ||
      !matchMedia("(hover: hover) and (pointer: fine)").matches
    )
      return;
    let frame = 0;
    let x = 0;
    let y = 0;
    function paint() {
      frame = 0;
      for (const card of el!.querySelectorAll<HTMLElement>(".bento-card")) {
        const r = card.getBoundingClientRect();
        card.style.setProperty("--mx", `${x - r.left}px`);
        card.style.setProperty("--my", `${y - r.top}px`);
      }
    }
    const move = (e: PointerEvent) => {
      x = e.clientX;
      y = e.clientY;
      if (!frame) frame = requestAnimationFrame(paint);
    };
    const enter = () => el.classList.add("is-lit");
    const leave = () => el.classList.remove("is-lit");
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerenter", enter);
    el.addEventListener("pointerleave", leave);
    return () => {
      cancelAnimationFrame(frame);
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerenter", enter);
      el.removeEventListener("pointerleave", leave);
    };
  }, [reduced]);

  return (
    <div className="bento" ref={ref}>
      <Card className="bento-download">
        <div className="bento-topline">
          <span className="bento-icon sun-icon">
            <Zap size={20} aria-hidden="true" />
          </span>
          <span className="feature-badge">ÍT CHỜ ĐỢI HƠN</span>
        </div>
        <h3>
          6 luồng tải.
          <br />
          Cảm hứng không phải đợi.
        </h3>
        <p>
          Tải song song HTTP Range khi nguồn hỗ trợ, tự về một kết nối khi cần.
          Trình duyệt chạy ẩn với <code>--headless=new</code>; chỉ hiện khi cần
          đăng nhập hoặc giải CAPTCHA.
        </p>
        <div className="download-art" aria-hidden="true">
          <div className="download-art-label">
            <span>HTTP RANGE</span>
            <span>06 CONNECTIONS</span>
          </div>
          <div className="range-streams">
            {Array.from({ length: 6 }, (_, i) => (
              <span key={i}>
                <i
                  style={{
                    animationDelay: `${i * -0.6}s`,
                    width: `${40 + i * 9}%`,
                  }}
                />
                <b>0{i + 1}</b>
              </span>
            ))}
          </div>
          <span className="range-complete">
            <Check size={12} /> Tự tiếp tục phần đang tải
          </span>
        </div>
      </Card>
      <Card className="bento-render">
        <span className="bento-icon mint-icon">
          <Cpu size={21} aria-hidden="true" />
        </span>
        <div className="gpu-art" aria-hidden="true">
          <div className="gpu-chip">
            <Cpu size={38} strokeWidth={1.1} />
            <span>NVENC</span>
          </div>
          <i />
          <i />
          <i />
        </div>
        <h3>
          Nhẹ thời gian render.
          <br />
          Nặng chất sáng tạo.
        </h3>
        <p>
          Tăng tốc phần cứng NVIDIA <code>h264_nvenc</code>. Kiểm tra encoder
          thực tế và tự dùng CPU khi GPU không đáp ứng.
        </p>
        <span className="bento-footnote">
          <span className="live-dot" /> GPU khi sẵn sàng. CPU luôn có lối.
        </span>
      </Card>
      <Card className="bento-edit">
        <span className="bento-icon coral-icon">
          <Clapperboard size={20} aria-hidden="true" />
        </span>
        <div className="edit-art" aria-hidden="true">
          <div className="edit-caption">
            <span lang="zh-CN">让故事被听见</span>
            <strong>Để câu chuyện được lắng nghe.</strong>
          </div>
          <div className="audio-art">
            {Array.from({ length: 33 }, (_, i) => (
              <i key={i} style={{ height: `${12 + ((i * 17) % 31)}px` }} />
            ))}
          </div>
          <span>
            <AudioLines size={12} /> CapCut TTS + BGM
          </span>
        </div>
        <h3>
          Không chỉ dịch lời.
          <br />
          Trao một giọng kể.
        </h3>
        <p>
          Che chữ theo vùng OCR, đóng phụ đề đẹp, lồng tiếng CapCut và đổi tốc
          độ / cao độ. Tách giọng là tùy chọn cần model ngoài; chất lượng nhạc
          nền tùy model.
        </p>
        <span className="bento-footnote">
          CapCut là API không chính thức, có thể thay đổi.
        </span>
      </Card>
      <Card className="bento-storage">
        <span className="bento-icon sun-icon">
          <HardDrive size={20} aria-hidden="true" />
        </span>
        <h3>
          Dọn dung lượng.
          <br />
          Giữ lại công sức.
        </h3>
        <p>
          Xem trước dung lượng, chọn xóa video nguồn hàng loạt. Giữ{" "}
          <code>.vi.srt</code> mặc định và bảo vệ tài nguyên còn được dùng.
        </p>
        <div className="storage-art" aria-hidden="true">
          <div>
            <span>
              <Clapperboard size={15} /> video gốc
            </span>
            <span className="storage-muted">Dọn theo lựa chọn</span>
          </div>
          <div>
            <span>
              <FileCheck2 size={15} /> story.vi.srt
            </span>
            <span className="storage-keep">
              <Check size={12} /> Giữ lại
            </span>
          </div>
        </div>
        <span className="bento-footnote">
          Luôn xem kỹ danh sách trước khi xóa file.
        </span>
      </Card>
      <Card className="bento-publish">
        <div className="bento-topline">
          <span className="bento-icon mint-icon">
            <Layers3 size={20} aria-hidden="true" />
          </span>
          <span className="feature-badge mint-badge">PUBLISH HUB</span>
        </div>
        <h3>
          Mỗi tài khoản, một không gian riêng.
          <br />
          Mỗi câu chuyện, nhiều điểm đến.
        </h3>
        <p>
          Chuyển video đã dựng, chỉnh metadata, chọn nhiều profile và xếp lịch
          đăng cục bộ. Trạng thái từng tài khoản độc lập; cần app và máy tính
          đang chạy.
        </p>
        <div className="profile-art" aria-hidden="true">
          {[
            { text: "TK", name: "TikTok", color: "sun" },
            { text: "YT", name: "YouTube", color: "coral" },
            { text: "FB", name: "Facebook Page", color: "mint" },
          ].map((p) => (
            <div className={`profile-mini ${p.color}`} key={p.text}>
              <span>{p.text}</span>
              <div>
                <strong>{p.name}</strong>
                <small>
                  <LockKeyhole size={10} /> Profile độc lập
                </small>
              </div>
              <Check size={13} />
            </div>
          ))}
        </div>
      </Card>
      <div className="bento-honesty">
        <ScanText size={17} aria-hidden="true" />
        <p>
          <strong>Đúng những gì có trong v2.0.2.</strong> Xuất MP4, SRT và gói
          CapCut Draft thử nghiệm. Cần kiểm tra với phiên bản CapCut đang dùng;
          không bảo đảm tương thích mọi phiên bản.
        </p>
        <Sparkles size={17} aria-hidden="true" />
      </div>
    </div>
  );
}
