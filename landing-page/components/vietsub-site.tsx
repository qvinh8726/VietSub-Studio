import { ArrowRight, ArrowUpRight, Check, Download, GitBranch, Play, ShieldCheck } from "lucide-react";
import Image from "next/image";
import { BentoGrid } from "./landing/bento-grid";
import { CHANGELOG_URL, FAQS, METRICS, PLATFORM_ITEMS, RELEASE_URL, REPO_URL, REQUIREMENTS, TECH_ITEMS, VERSION } from "./landing/data";
import { Marquee } from "./landing/marquee";
import { MotionRoot } from "./landing/motion-root";
import { PipelineBeam } from "./landing/pipeline-beam";
import { Reveal } from "./landing/reveal";
import { SakugaCta, SakugaScript } from "./landing/sakuga";
import { SiteHeader } from "./landing/site-header";
import { SubtitleSandbox } from "./landing/subtitle-sandbox";

function SectionHead({ kicker, title, body, id }: { kicker: string; title: string; body?: string; id: string }) {
  return (
    <Reveal className="section-head">
      <p className="kicker">{kicker}</p>
      <h2 id={id}>{title}</h2>
      {body ? <p className="section-body">{body}</p> : null}
    </Reveal>
  );
}

export default function VietSubSite() {
  return (
    <MotionRoot>
      <SakugaScript />
      <a href="#main" className="skip-link">
        Bỏ qua, tới nội dung chính
      </a>
      <SiteHeader />

      <main id="main">
        <section className="hero" id="top" aria-labelledby="hero-title">
          <div className="hero-backdrop" aria-hidden="true" />
          <div className="wrap hero-copy">
            <div className="hero-lead">
              <a className="release-pill" href={RELEASE_URL} target="_blank" rel="noreferrer">
                <span className="release-tag">v{VERSION}</span>
                Tải 6 kết nối · Douyin bitrate vừa đủ cho OCR
                <ArrowUpRight size={14} aria-hidden="true" />
              </a>
              <h1 id="hero-title">
                Video Trung có hardsub, ra file <span className="hl-sun">.vi.srt</span> đúng từng timestamp.
              </h1>
            </div>
            <div className="hero-side">
              <p className="hero-sub">
                Dán link share 抖音, B站, 小红书, 快手 hoặc mở MP4 trên máy. VietSub Studio tải bản gốc, OCR đúng vùng chữ bằng VideOCR rồi
                dịch sang tiếng Việt bằng DeepSeek hoặc Gemini, giữ nguyên index và timecode.
              </p>
              <div className="hero-actions">
                <SakugaCta href={RELEASE_URL} ariaLabel={`Tải VietSub Studio v${VERSION} trên GitHub Releases (mở tab mới)`}>
                  <Download size={19} aria-hidden="true" /> Tải VietSub Studio v{VERSION}
                </SakugaCta>
                <a href="#demo" className="btn-ghost btn-lg">
                  <Play size={16} aria-hidden="true" /> Thử sandbox
                </a>
              </div>
              <ul className="trust">
                <li>
                  <Check size={15} aria-hidden="true" /> Windows 10/11 x64, bản portable
                </li>
                <li>
                  <Check size={15} aria-hidden="true" /> Mã nguồn mở MIT
                </li>
                <li>
                  <Check size={15} aria-hidden="true" /> Không gửi video lên máy chủ của app
                </li>
              </ul>
            </div>
          </div>

          <div className="wrap" id="demo">
            <SubtitleSandbox />
            <p className="sandbox-note">
              Kéo khung vàng lên xuống hoặc dùng phím mũi tên để xem OCR đọc gì. Đây là mô phỏng trên trình duyệt: app thật chạy VideOCR và
              FFmpeg trên máy bạn.
            </p>
          </div>
        </section>

        <section className="metrics" aria-labelledby="metrics-title">
          <div className="wrap">
            <h2 id="metrics-title" className="sr-only">
              Số liệu của v{VERSION}
            </h2>
            <dl className="metric-grid">
              {METRICS.map((m) => (
                <div key={m.value} className="metric">
                  <dt>{m.label}</dt>
                  <dd>{m.value}</dd>
                </div>
              ))}
            </dl>
            <div className="metrics-cta">
              <a href={CHANGELOG_URL} target="_blank" rel="noreferrer" className="text-link">
                Đọc changelog v{VERSION} <ArrowUpRight size={15} aria-hidden="true" />
              </a>
              <a href={RELEASE_URL} target="_blank" rel="noreferrer" className="btn-sun-sm">
                <Download size={16} aria-hidden="true" /> Tải bản portable
              </a>
            </div>
          </div>
        </section>

        <section className="marquee-section" aria-label="Nguồn video và công nghệ">
          <Marquee platforms={PLATFORM_ITEMS} tech={TECH_ITEMS} />
        </section>

        <section className="section" id="pipeline" aria-labelledby="pipeline-title">
          <div className="wrap">
            <SectionHead
              id="pipeline-title"
              kicker="Quy trình"
              title="Từ một đoạn share tới file phụ đề, trong một cửa sổ."
              body="Mỗi bước có kiểm tra riêng. Lỗi ở bước nào thì app dừng ở bước đó và nói rõ cần làm gì."
            />
            <PipelineBeam />
          </div>
        </section>

        <section className="section" id="features" aria-labelledby="features-title">
          <div className="wrap">
            <SectionHead
              id="features-title"
              kicker="Tính năng"
              title="Làm phần việc lặp lại, để bạn chỉ còn duyệt bản dịch."
            />
            <BentoGrid />
          </div>
        </section>

        <section className="section" id="faq" aria-labelledby="faq-title">
          <div className="wrap faq-layout">
            <div>
              <SectionHead id="faq-title" kicker="Trước khi cài" title="Cần gì để chạy, và những câu hay gặp." />
              <Reveal className="req-card" delay={0.05}>
                <h3>Bắt buộc</h3>
                <ul>
                  {REQUIREMENTS.required.map((r) => (
                    <li key={r}>
                      <Check size={15} aria-hidden="true" /> {r}
                    </li>
                  ))}
                </ul>
                <h3>Nên có</h3>
                <ul className="is-soft">
                  {REQUIREMENTS.recommended.map((r) => (
                    <li key={r}>
                      <ArrowRight size={15} aria-hidden="true" /> {r}
                    </li>
                  ))}
                </ul>
              </Reveal>
            </div>
            <div className="faq-list">
              {FAQS.map((f) => (
                <details key={f.q} className="faq-item">
                  <summary>{f.q}</summary>
                  <p>{f.a}</p>
                </details>
              ))}
            </div>
          </div>
        </section>

        <section className="final" aria-labelledby="final-title">
          <div className="wrap final-card">
            <div>
              <p className="kicker">VietSub Studio v{VERSION}</p>
              <h2 id="final-title">Thử với video đầu tiên của bạn.</h2>
              <p className="section-body">
                Tải bản portable, mở app, dán link. Các bản sau app tự kiểm tra cập nhật và so SHA256 trước khi thay file.
              </p>
            </div>
            <div className="final-actions">
              <SakugaCta href={RELEASE_URL} ariaLabel={`Tải VietSub Studio v${VERSION} trên GitHub Releases (mở tab mới)`}>
                <Download size={19} aria-hidden="true" /> Tải VietSub Studio v{VERSION}
              </SakugaCta>
              <span className="final-meta">
                <ShieldCheck size={15} aria-hidden="true" /> Windows 10/11 x64 · MIT License
              </span>
            </div>
          </div>
        </section>
      </main>

      <footer className="footer">
        <div className="wrap footer-row">
          <a href="#top" className="brand">
            <Image src="/avatar.jpg" width={28} height={28} alt="" className="brand-mark" />
            <span>VietSub Studio</span>
          </a>
          <nav aria-label="Liên kết cuối trang" className="footer-links">
            <a href={REPO_URL} target="_blank" rel="noreferrer">
              <GitBranch size={15} aria-hidden="true" /> GitHub
            </a>
            <a href={RELEASE_URL} target="_blank" rel="noreferrer">
              Release v{VERSION}
            </a>
            <a href={CHANGELOG_URL} target="_blank" rel="noreferrer">
              Changelog
            </a>
          </nav>
          <p className="footer-note">Mã nguồn mở MIT. Hiệu ứng nút và tab dùng Sakuga VFX (MIT).</p>
        </div>
      </footer>
    </MotionRoot>
  );
}
