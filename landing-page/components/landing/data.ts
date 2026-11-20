// One source of truth for every download surface, including the legacy relay entry.
export const VERSION = "2.0.2";
export const REPO_URL = "https://github.com/qvinh8726/VietSub-Studio";
export const BUILD_URL = `${REPO_URL}/actions`;
export const RELEASE_URL = `${REPO_URL}/releases/tag/v${VERSION}`;
export const CHECKSUM_URL = `${REPO_URL}/releases/download/v${VERSION}/VietSub-Studio-Portable-v${VERSION}.zip.sha256.txt`;
export const CHANGELOG_URL = `${REPO_URL}/blob/main/CHANGELOG.md`;

export type SubtitleLine = {
  start: number;
  end: number;
  zh: string;
  vi: string;
};

export type SandboxPlatform = {
  id: "douyin" | "bilibili" | "xiaohongshu" | "kuaishou";
  label: string;
  han: string;
  shareUrl: string;
  /** Width / height of the video. */
  ratio: number;
  /** Where the burned-in subtitle sits, as fractions of the video height. */
  band: { top: number; height: number };
  scene: string;
  glow: string;
  watermark: string;
  /** Text elsewhere in the frame that OCR picks up when it scans the whole frame. */
  noise: string;
  icon: "headphones" | "cpu" | "coffee" | "pot";
  lines: SubtitleLine[];
};

export const SANDBOX_LOOP_SECONDS = 10.2;

export const SANDBOX_PLATFORMS: SandboxPlatform[] = [
  {
    id: "douyin",
    label: "Douyin",
    han: "抖音",
    shareUrl:
      "4.89 复制打开抖音，看看【VietSub演示】https://v.douyin.com/demo-vs/",
    ratio: 9 / 16,
    band: { top: 0.79, height: 0.09 },
    scene: "linear-gradient(165deg, #1d2b52 0%, #3b1f45 52%, #0d1322 100%)",
    glow: "radial-gradient(60% 40% at 30% 30%, rgba(255, 64, 129, 0.35), transparent 70%), radial-gradient(50% 35% at 75% 60%, rgba(37, 244, 238, 0.22), transparent 70%)",
    watermark: "抖音号：VietSub演示",
    noise: "抖音号：VietSub演示",
    icon: "headphones",
    lines: [
      {
        start: 1.2,
        end: 3.8,
        zh: "大家好，今天带你们看看这款新耳机。",
        vi: "Chào mọi người, hôm nay mình cho các bạn xem chiếc tai nghe mới này.",
      },
      {
        start: 4.1,
        end: 6.6,
        zh: "音质真的很清楚，低音也很足。",
        vi: "Âm thanh thật sự rất rõ, âm trầm cũng rất đầy.",
      },
      {
        start: 7.0,
        end: 9.6,
        zh: "喜欢的话记得点赞关注哦！",
        vi: "Nếu thích thì nhớ thả tim và theo dõi nhé!",
      },
    ],
  },
  {
    id: "bilibili",
    label: "Bilibili",
    han: "哔哩哔哩",
    shareUrl: "【新显卡4K实测】https://b23.tv/demo-vs",
    ratio: 16 / 9,
    band: { top: 0.8, height: 0.12 },
    scene: "linear-gradient(160deg, #0b2a3c 0%, #164a63 55%, #071a26 100%)",
    glow: "radial-gradient(55% 45% at 70% 35%, rgba(0, 174, 236, 0.32), transparent 70%), radial-gradient(40% 35% at 20% 70%, rgba(251, 114, 153, 0.22), transparent 70%)",
    watermark: "BV1VietSub2026",
    noise: "弹幕：太强了！",
    icon: "cpu",
    lines: [
      {
        start: 1.2,
        end: 3.8,
        zh: "这一期我们来测试新显卡的性能。",
        vi: "Số này chúng ta sẽ thử hiệu năng của card đồ hoạ mới.",
      },
      {
        start: 4.1,
        end: 6.6,
        zh: "在4K画质下帧数依然很稳定。",
        vi: "Ở chất lượng 4K, khung hình vẫn rất ổn định.",
      },
      {
        start: 7.0,
        end: 9.6,
        zh: "别忘了一键三连支持一下！",
        vi: "Đừng quên like, tặng xu và lưu video để ủng hộ nhé!",
      },
    ],
  },
  {
    id: "xiaohongshu",
    label: "Xiaohongshu",
    han: "小红书",
    shareUrl: "小巷里的宝藏咖啡店 http://xhslink.com/demo-vs",
    ratio: 9 / 16,
    band: { top: 0.76, height: 0.09 },
    scene: "linear-gradient(165deg, #4a2b1d 0%, #6b3a24 45%, #24150f 100%)",
    glow: "radial-gradient(55% 40% at 35% 35%, rgba(255, 196, 120, 0.32), transparent 70%), radial-gradient(45% 35% at 70% 70%, rgba(255, 36, 66, 0.18), transparent 70%)",
    watermark: "小红书号：2026demo",
    noise: "小红书号：2026demo",
    icon: "coffee",
    lines: [
      {
        start: 1.2,
        end: 3.8,
        zh: "这家咖啡店藏在小巷子里。",
        vi: "Quán cà phê này nằm khuất trong một con hẻm nhỏ.",
      },
      {
        start: 4.1,
        end: 6.6,
        zh: "拿铁的奶泡特别绵密。",
        vi: "Lớp bọt sữa của ly latte cực kỳ mịn.",
      },
      {
        start: 7.0,
        end: 9.6,
        zh: "周末来打卡一定不会失望。",
        vi: "Cuối tuần ghé check-in chắc chắn sẽ không thất vọng.",
      },
    ],
  },
  {
    id: "kuaishou",
    label: "Kuaishou",
    han: "快手",
    shareUrl: "家常红烧肉做法 https://v.kuaishou.com/demo-vs",
    ratio: 9 / 16,
    band: { top: 0.8, height: 0.09 },
    scene: "linear-gradient(165deg, #3f2a0f 0%, #5e3a12 50%, #1f150a 100%)",
    glow: "radial-gradient(55% 40% at 40% 35%, rgba(255, 128, 32, 0.35), transparent 70%), radial-gradient(40% 30% at 70% 65%, rgba(255, 214, 102, 0.2), transparent 70%)",
    watermark: "快手号：demo2026",
    noise: "快手号：demo2026",
    icon: "pot",
    lines: [
      {
        start: 1.2,
        end: 3.8,
        zh: "今天教大家做一道家常红烧肉。",
        vi: "Hôm nay mình dạy mọi người làm món thịt kho tàu kiểu gia đình.",
      },
      {
        start: 4.1,
        end: 6.6,
        zh: "先把五花肉切成小块焯水。",
        vi: "Trước tiên thái thịt ba chỉ thành miếng nhỏ rồi chần qua nước sôi.",
      },
      {
        start: 7.0,
        end: 9.6,
        zh: "小火慢炖四十分钟就好了。",
        vi: "Hầm lửa nhỏ bốn mươi phút là xong.",
      },
    ],
  },
];

export type MarqueeItem = { han?: string; label: string; note?: string };

/** Source integrations and publishing channels are intentionally described separately on the page. */
export const PLATFORM_ITEMS: MarqueeItem[] = [
  { han: "抖音", label: "Douyin" },
  { han: "哔哩哔哩", label: "Bilibili" },
  { han: "小红书", label: "Xiaohongshu" },
  { han: "快手", label: "Kuaishou", note: "source" },
  { label: "TikTok" },
  { label: "YouTube Shorts" },
  { label: "Facebook Reels", note: "Page" },
];

export const TECH_ITEMS: MarqueeItem[] = [
  { label: "VietSub" },
  { label: "Auto Edit" },
  { label: "Publish Hub" },
  { label: "CapCut TTS" },
  { label: "NVIDIA NVENC" },
  { label: ".vi.srt" },
  { label: "HTTP Range × 6" },
  { label: "FFmpeg" },
];

export const PIPELINE_STEPS = [
  {
    title: "Hiểu từng lời kể.",
    label: "VIETSUB THÔNG MINH",
    body: "Dán link hoặc nhập MP4/MKV. Tải 6 luồng khi nguồn hỗ trợ HTTP Range, chọn vùng OCR và dịch ra .vi.srt giữ timecode.",
    note: "Trình duyệt chạy ẩn · Hiện khi cần xác minh",
    icon: "scan",
  },
  {
    title: "Thêm dấu ấn riêng.",
    label: "AUTO EDIT",
    body: "Che chữ gốc theo vùng OCR, đóng phụ đề, lồng tiếng CapCut và chỉnh tốc độ / cao độ. Render với NVENC nếu GPU hỗ trợ.",
    note: "CapCut TTS · FFmpeg · CPU fallback",
    icon: "edit",
  },
  {
    title: "Đưa câu chuyện đi xa.",
    label: "ĐĂNG ĐA NỀN TẢNG",
    body: "Chuyển video đã dựng sang Publish Hub, duyệt nội dung, chọn các profile độc lập và xếp lịch đăng theo từng tài khoản.",
    note: "Lịch cục bộ · Cần app và máy đang chạy",
    icon: "publish",
  },
] as const;

export const FAQS = [
  {
    q: "Tôi cần chuẩn bị gì để bắt đầu?",
    a: "Bản portable chạy trên Windows 10/11 x64. Tải ZIP từ GitHub Releases v2.0.2 và giải nén. Cài riêng VideOCR CLI cho OCR, FFmpeg + FFprobe cho xử lý video, và Chrome/Edge cho tác vụ cần trình duyệt. Chọn API dịch hoặc phiên Gemini Notebook của bạn; Deno/Node 22+ cần cho nguồn YouTube. App không tự cài các model AI nặng.",
  },
  {
    q: "Chạy ẩn 100% có nghĩa là không cần đăng nhập?",
    a: "Không. Các tác vụ tự động bình thường dùng Chromium --headless=new với profile riêng. Khi gặp CAPTCHA hoặc cần đăng nhập, app hiện trình duyệt để bạn xác minh thủ công. Phiên đăng nhập hợp lệ được tái sử dụng; profile cá nhân hằng ngày của bạn không bị sửa. OCR và render chạy trên máy.",
  },
  {
    q: "CapCut TTS, tách giọng và CapCut Draft có sẵn không?",
    a: "v2.0.2 có lồng tiếng qua API CapCut, khớp giọng theo cue SRT, thay đổi tốc độ/cao độ và trộn nền. Đây là dịch vụ nội bộ không chính thức, giọng và khả năng truy cập có thể đổi. Tách giọng giữ nền cần runtime/model ngoài (audio-separator/ONNX), không kèm portable và có thể ảnh hưởng nhạc/hiệu ứng. v2.0.2 hỗ trợ xuất gói CapCut Draft thử nghiệm bên cạnh MP4 và SRT. Cần kiểm tra gói với phiên bản CapCut đang dùng; không bảo đảm tương thích mọi phiên bản.",
  },
  {
    q: "Hẹn giờ đăng có chạy khi tôi tắt máy không?",
    a: "Không. Lịch nằm cục bộ và cần app cùng máy tính đang chạy. Bạn đăng nhập thủ công cho từng profile, chọn tài khoản, duyệt metadata rồi xếp lịch. Khả năng đăng và chế độ riêng tư khác nhau theo nền tảng; Facebook hỗ trợ Page / Meta Business Suite, không phải mọi profile cá nhân. UI nền tảng thay đổi, CAPTCHA hay phiên hết hạn có thể cần bạn can thiệp. Không cam kết mọi lần tải lên đều thành công.",
  },
  {
    q: "Dọn video có làm mất phụ đề hay file gốc?",
    a: "Xóa bản ghi mặc định không xóa media. Nếu bạn bật xóa file, app xem trước dung lượng, giữ .vi.srt mặc định và bảo vệ file đang được job khác sử dụng. Video nguồn, kể cả file gốc trên máy nếu bạn chọn, có thể bị xóa: hãy xem kỹ trước khi xác nhận. Tự dọn sau khi dịch là tùy chọn mặc định tắt, chỉ chạy sau khi SRT được kiểm tra thành công.",
  },
  {
    q: "Tôi có cần card NVIDIA? Dữ liệu có ở trên máy không?",
    a: "Không bắt buộc: app kiểm tra NVENC thực tế và chuyển sang CPU nếu không dùng được. Video được OCR/render trên máy; văn bản phụ đề gửi đến AI dịch bạn cấu hình, văn bản TTS đến dịch vụ giọng nói, và video gửi đến nền tảng khi bạn yêu cầu đăng. Tài khoản và cookie thuộc các profile độc lập, không đi trong manifest project. Chỉ dùng nội dung bạn có quyền sử dụng.",
  },
];
