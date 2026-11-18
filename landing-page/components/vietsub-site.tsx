import { ArrowRight, ArrowUpRight, Check, Download, GitBranch, Play, ShieldCheck } from "lucide-react";
import Image from "next/image";
import { BentoGrid } from "./landing/bento-grid";
import { BUILD_URL, CHANGELOG_URL, CHECKSUM_URL, FAQS, METRICS, PLATFORM_ITEMS, RELEASE_URL, REPO_URL, REQUIREMENTS, TECH_ITEMS, VERSION } from "./landing/data";
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

function DownloadNote() {
  return (
    <p className="download-note">
      Bản portable: tải ZIP, giải nén rồi mở app.
      {" "}<a href={CHECKSUM_URL} target="_blank" rel="noreferrer">Checksum SHA256</a> đi kèm.
    </p>
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
              <a className="release-pill" href={CHANGELOG_URL} target="_blank" rel="noreferrer">
                <span className="release-tag">v{VERSION}</span>
                <span className="release-summary">Batch import · Dọn dung lượng · Publish pack</span>
                <ArrowUpRight size={14} aria-hidden="true" />
              </a>
              <h1 id="hero-title">
                Video Trung có hardsub, ra file <span className="hl-sun">.vi.srt</span> đúng từng timestamp.
              </h1>
            </div>
            <div className="hero-side">
              <p className="hero-sub">
                Dán nhiều link share hoặc chọn cả loạt MP4/MKV. VietSub Studio xử lý lần lượt: tải video, OCR đúng vùng chữ,
                dịch sang tiếng Việt và giữ nguyên timecode. Khi xong, bạn chọn dọn video và giữ phụ đề cho bước dựng tiếp theo.
              </p>
              <div className="hero-actions">
                <SakugaCta href={RELEASE_URL} ariaLabel={`Tải VietSub Studio v${VERSION} trên GitHub Releases (mở tab mới)`}>
                  <Download size={19} aria-hidden="true" /> Tải VietSub Studio v{VERSION}
                </SakugaCta>
                <a href="#demo" className="btn-ghost btn-lg">
                  <Play size={16} aria-hidden="true" /> Thử sandbox
                </a>
              </div>
              <DownloadNote />
              <ul className="trust">
                <li>
                  <Check size={15} aria-hidden="true" /> Windows 10/11 x64, bản portable
                </li>
                <li>
                  <Check size={15} aria-hidden="true" /> Mã nguồn mở MIT
                </li>
                <li>
                  <Check size={15} aria-hidden="true" /> Video xử lý trên máy của bạn
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
              Mặc định và kiểm thử của v{VERSION}
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
              title="Từ cả loạt video tới từng bộ phụ đề, trong một cửa sổ."
              body="Workbench gom nguồn, preview, crop và hàng đợi trong một cửa sổ. Chọn một video để chỉnh crop riêng; nhập hàng loạt để dùng crop mặc định."
            />
            <PipelineBeam />
          </div>
        </section>

        <section className="section" id="features" aria-labelledby="features-title">
          <div className="wrap">
            <SectionHead
              id="features-title"
              kicker={`Có gì mới trong v${VERSION}`}
              title="Nhập nhiều hơn. Dọn gọn hơn. Sẵn nội dung để đăng."
              body="Giữ giao diện Studio Workbench, bổ sung thao tác hàng loạt và các tùy chọn cho bước sau khi xuất sub."
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
              <h2 id="final-title">Bắt đầu với hàng đợi tiếp theo của bạn.</h2>
              <p className="section-body">
                Mở bản portable v{VERSION}, nhập nhiều video và duyệt từng bộ phụ đề. Tự dọn nguồn và tạo publish pack chỉ chạy khi bạn bật trong Thiết lập.
              </p>
            </div>
            <div className="final-actions">
              <SakugaCta href={RELEASE_URL} ariaLabel={`Tải VietSub Studio v${VERSION} trên GitHub Releases (mở tab mới)`}>
                <Download size={19} aria-hidden="true" /> Tải VietSub Studio v{VERSION}
              </SakugaCta>
              <DownloadNote />
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
            <a href={BUILD_URL} target="_blank" rel="noreferrer">
              Kết quả CI
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
