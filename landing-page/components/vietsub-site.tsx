import {
  ArrowDown,
  ArrowRight,
  ArrowUpRight,
  Check,
  ChevronDown,
  Clapperboard,
  Download,
  GitBranch,
  Heart,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { BentoGrid } from "./landing/bento-grid";
import {
  CHANGELOG_URL,
  CHECKSUM_URL,
  FAQS,
  PLATFORM_ITEMS,
  RELEASE_URL,
  REPO_URL,
  TECH_ITEMS,
  VERSION,
} from "./landing/data";
import { Marquee } from "./landing/marquee";
import { MotionRoot } from "./landing/motion-root";
import { Atmosphere } from "./landing/atmosphere";
import { PipelineBeam } from "./landing/pipeline-beam";
import { Reveal } from "./landing/reveal";
import { SakugaCta, SakugaScript } from "./landing/sakuga";
import { SiteHeader } from "./landing/site-header";
import { StudioShowcase } from "./landing/studio-showcase";

function DownloadNote() {
  return (
    <p className="download-note">
      Windows 10/11 x64 <span>·</span> Portable <span>·</span> Mã nguồn mở MIT
    </p>
  );
}

export default function VietSubSite() {
  return (
    <MotionRoot>
      <SakugaScript />
      <a href="#main" className="skip-link">
        Tới nội dung chính
      </a>
      <SiteHeader />
      <main id="main">
        <section className="hero" id="top" aria-labelledby="hero-title">
          <Atmosphere />
          <div className="wrap hero-copy">
            <Reveal className="hero-lead">
              <a
                className="release-pill"
                href={CHANGELOG_URL}
                target="_blank"
                rel="noreferrer"
              >
                <span className="release-tag">MỚI · v{VERSION}</span>
                <span>Không gian sáng tạo 3-trong-1</span>
                <ArrowUpRight size={14} aria-hidden="true" />
              </a>
              <h1 id="hero-title">
                Một ý tưởng.
                <br />
                <span className="hero-emphasis">Muôn cách tỏa sáng.</span>
              </h1>
              <p className="hero-workflow">
                <span>VietSub</span>
                <ArrowRight size={12} aria-hidden="true" />
                <span>Auto Edit</span>
                <ArrowRight size={12} aria-hidden="true" />
                <span>Đăng đa nền tảng</span>
              </p>
              <p className="hero-sub">
                Từ thước phim bạn yêu đến câu chuyện mang dấu ấn riêng.
                <br className="desktop-break" /> Liền mạch trong một Creator
                Workbench. Dành riêng cho bạn.
              </p>
              <div className="hero-actions">
                <SakugaCta
                  href={RELEASE_URL}
                  ariaLabel={`Tải miễn phí v${VERSION} — VietSub Studio trên GitHub Releases (mở tab mới)`}
                >
                  <Download size={18} aria-hidden="true" /> Tải miễn phí v
                  {VERSION} <ArrowUpRight size={17} aria-hidden="true" />
                </SakugaCta>
                <a href="#demo" className="btn-ghost btn-lg">
                  Khám phá Studio <ArrowDown size={16} aria-hidden="true" />
                </a>
              </div>
              <DownloadNote />
            </Reveal>
          </div>
          <div className="wrap hero-studio" id="demo">
            <div className="showcase-label-row">
              <span className="eyebrow">
                <span className="live-dot" /> MỘT STUDIO. BA KHẢ NĂNG.
              </span>
              <span className="studio-invite">
                Chạm để khám phá <ArrowDown size={13} aria-hidden="true" />
              </span>
            </div>
            <StudioShowcase />
          </div>
          <div className="wrap hero-footnote">
            <span>Tập trung vào câu chuyện.</span>
            <span>
              Để Studio nối những phần còn lại.{" "}
              <span className="tiny-star" aria-hidden="true">
                ✦
              </span>
            </span>
          </div>
        </section>

        <section
          className="section workflow-section"
          id="pipeline"
          aria-labelledby="pipeline-title"
        >
          <div className="wrap">
            <Reveal className="section-head centered">
              <p className="kicker">
                <span /> TỪ CẢM HỨNG ĐẾN LÊN SÓNG
              </p>
              <h2 id="pipeline-title">
                Giữ mạch sáng tạo.
                <br />
                <span className="serif-note">Bớt những bước rời rạc.</span>
              </h2>
              <p className="section-body">
                Dán link, trao cho video một giọng kể mới, rồi đưa câu chuyện
                đến nhiều nơi hơn. Không phải bắt đầu lại ở mỗi bước.
              </p>
            </Reveal>
            <PipelineBeam />
            <Reveal className="workflow-caption">
              <ShieldCheck size={15} aria-hidden="true" />
              <p>
                Mỗi chặng giữ trạng thái riêng trong project. Bạn luôn là người
                duyệt nội dung và lựa chọn khi nào xuất bản.
              </p>
            </Reveal>
          </div>
        </section>

        <section
          className="section features-section"
          id="features"
          aria-labelledby="features-title"
        >
          <div className="wrap">
            <Reveal className="section-head split-head">
              <div>
                <p className="kicker">
                  <span /> ĐƯỢC CHĂM CHÚT, TỪNG CHI TIẾT
                </p>
                <h2 id="features-title">
                  Mượt ở trải nghiệm.
                  <br />
                  <span className="serif-note">Mạnh ở bên trong.</span>
                </h2>
              </div>
              <p className="section-body">
                Những nâng cấp của v{VERSION} để công việc nhẹ đi — và khoảng
                trống cho sáng tạo rộng hơn.
              </p>
            </Reveal>
            <BentoGrid />
          </div>
        </section>

        <section className="platform-section" aria-labelledby="platform-title">
          <div className="wrap">
            <Reveal className="platform-head">
              <p className="kicker">MỘT CÂU CHUYỆN, NHIỀU ĐIỂM CHẠM</p>
              <h2 id="platform-title">
                Được tìm thấy.{" "}
                <span className="serif-note">Ở nhiều nơi hơn.</span>
              </h2>
              <p>
                Nguồn video quen thuộc, điểm đến đa dạng. Profile riêng cho từng
                tài khoản.
              </p>
            </Reveal>
          </div>
          <Marquee platforms={PLATFORM_ITEMS} tech={TECH_ITEMS} />
          <p className="platform-note wrap">
            Nguồn tải và kênh đăng có phạm vi khác nhau. Khả năng đăng, quyền
            riêng tư và xác minh phụ thuộc từng nền tảng; Facebook dùng Page /
            Meta Business Suite.
          </p>
        </section>

        <section
          className="section faq-section"
          id="faq"
          aria-labelledby="faq-title"
        >
          <div className="wrap faq-layout">
            <Reveal className="faq-intro">
              <p className="kicker">
                <span /> TRƯỚC KHI BẤM BẮT ĐẦU
              </p>
              <h2 id="faq-title">
                Một vài điều <br />
                <span className="serif-note">bạn muốn biết.</span>
              </h2>
              <p className="section-body">
                Công cụ tốt bắt đầu từ lời giới thiệu rõ ràng. Đây là những gì
                Studio làm được — và những gì vẫn cần bạn.
              </p>
              <a
                href={`${REPO_URL}#readme`}
                target="_blank"
                rel="noreferrer"
                className="text-link"
              >
                Đọc hướng dẫn trên GitHub{" "}
                <ArrowUpRight size={15} aria-hidden="true" />
              </a>
              <div className="requirements-note">
                <ShieldCheck size={19} aria-hidden="true" />
                <p>
                  <strong>Video xử lý trên máy bạn.</strong>
                  <br />
                  Văn bản gửi đến AI bạn chọn; TTS gọi dịch vụ giọng nói. Video
                  chỉ được tải lên kênh khi bạn yêu cầu đăng.
                </p>
              </div>
            </Reveal>
            <div className="faq-list">
              {FAQS.map((f) => (
                <details key={f.q} className="faq-item">
                  <summary>
                    <span>{f.q}</span>
                    <ChevronDown size={17} aria-hidden="true" />
                  </summary>
                  <p>{f.a}</p>
                </details>
              ))}
            </div>
          </div>
        </section>

        <section className="final" id="download" aria-labelledby="final-title">
          <div className="wrap">
            <Reveal className="final-card">
              <div className="final-orbit orbit-one" aria-hidden="true" />
              <div className="final-orbit orbit-two" aria-hidden="true" />
              <div className="final-symbol" aria-hidden="true">
                <Clapperboard size={28} strokeWidth={1.4} />
                <Sparkles size={17} />
              </div>
              <p className="kicker">VIETSUB STUDIO · v{VERSION}</p>
              <h2 id="final-title">
                Câu chuyện tiếp theo.
                <br />
                <span>Hãy để nó là của bạn.</span>
              </h2>
              <p className="section-body">
                Một đường link. Một chút cảm hứng.
                <br />
                Và cả một Studio để bạn bắt đầu.
              </p>
              <div className="final-actions">
                <SakugaCta href={RELEASE_URL}>
                  <Download size={18} aria-hidden="true" /> Tải VietSub Studio v
                  {VERSION} <ArrowUpRight size={17} aria-hidden="true" />
                </SakugaCta>
                <DownloadNote />
              </div>
              <p className="final-install">
                Tải ZIP từ GitHub Releases → giải nén → mở app.
                <br />
                <a href={CHECKSUM_URL} target="_blank" rel="noreferrer">
                  Kiểm tra SHA256 <ArrowUpRight size={11} aria-hidden="true" />
                </a>
                <span>
                  VideOCR, FFmpeg và trình duyệt cài riêng theo nhu cầu.
                </span>
              </p>
            </Reveal>
          </div>
        </section>
      </main>
      <footer className="footer">
        <div className="wrap">
          <div className="footer-main">
            <div>
              <a href="#top" className="brand">
                <span className="brand-symbol">
                  <Clapperboard
                    size={21}
                    strokeWidth={1.7}
                    aria-hidden="true"
                  />
                </span>
                <span>
                  VietSub<span className="brand-light"> Studio</span>
                </span>
              </a>
              <p>Không gian cho những câu chuyện mới.</p>
            </div>
            <nav aria-label="Liên kết cuối trang">
              <a href="#demo">
                Khám phá Studio <ArrowRight size={14} aria-hidden="true" />
              </a>
              <a href={REPO_URL} target="_blank" rel="noreferrer">
                <GitBranch size={14} aria-hidden="true" /> Mã nguồn GitHub
              </a>
              <a href={CHANGELOG_URL} target="_blank" rel="noreferrer">
                Changelog <ArrowUpRight size={13} aria-hidden="true" />
              </a>
            </nav>
          </div>
          <div className="footer-bottom">
            <span>
              VietSub Studio © {new Date().getFullYear()} · MIT License
            </span>
            <span>
              Được làm với sự chăm chút <Heart size={12} aria-hidden="true" />{" "}
              Sakuga VFX · MIT
            </span>
            <a href={RELEASE_URL} target="_blank" rel="noreferrer">
              <Check size={12} aria-hidden="true" /> v{VERSION}
            </a>
          </div>
        </div>
      </footer>
    </MotionRoot>
  );
}
