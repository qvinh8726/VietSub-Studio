// Content of the VietSub Studio landing page. Numbers come from the v1.6.2 release notes and tests.

export const VERSION = "1.6.2";
export const RELEASE_URL = "https://github.com/qvinh8726/VietSub-Studio/releases/tag/v1.6.2";
export const REPO_URL = "https://github.com/qvinh8726/VietSub-Studio";
export const CHANGELOG_URL = "https://github.com/qvinh8726/VietSub-Studio/blob/main/CHANGELOG.md";

export type SubtitleLine = { start: number; end: number; zh: string; vi: string };

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
    shareUrl: "4.89 复制打开抖音，看看【VietSub演示】https://v.douyin.com/demo-vs/",
    ratio: 9 / 16,
    band: { top: 0.79, height: 0.09 },
    scene: "linear-gradient(165deg, #1d2b52 0%, #3b1f45 52%, #0d1322 100%)",
    glow: "radial-gradient(60% 40% at 30% 30%, rgba(255, 64, 129, 0.35), transparent 70%), radial-gradient(50% 35% at 75% 60%, rgba(37, 244, 238, 0.22), transparent 70%)",
    watermark: "抖音号：VietSub演示",
    noise: "抖音号：VietSub演示",
    icon: "headphones",
    lines: [
      { start: 1.2, end: 3.8, zh: "大家好，今天带你们看看这款新耳机。", vi: "Chào mọi người, hôm nay mình cho các bạn xem chiếc tai nghe mới này." },
      { start: 4.1, end: 6.6, zh: "音质真的很清楚，低音也很足。", vi: "Âm thanh thật sự rất rõ, âm trầm cũng rất đầy." },
      { start: 7.0, end: 9.6, zh: "喜欢的话记得点赞关注哦！", vi: "Nếu thích thì nhớ thả tim và theo dõi nhé!" },
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
      { start: 1.2, end: 3.8, zh: "这一期我们来测试新显卡的性能。", vi: "Số này chúng ta sẽ thử hiệu năng của card đồ hoạ mới." },
      { start: 4.1, end: 6.6, zh: "在4K画质下帧数依然很稳定。", vi: "Ở chất lượng 4K, khung hình vẫn rất ổn định." },
      { start: 7.0, end: 9.6, zh: "别忘了一键三连支持一下！", vi: "Đừng quên like, tặng xu và lưu video để ủng hộ nhé!" },
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
      { start: 1.2, end: 3.8, zh: "这家咖啡店藏在小巷子里。", vi: "Quán cà phê này nằm khuất trong một con hẻm nhỏ." },
      { start: 4.1, end: 6.6, zh: "拿铁的奶泡特别绵密。", vi: "Lớp bọt sữa của ly latte cực kỳ mịn." },
      { start: 7.0, end: 9.6, zh: "周末来打卡一定不会失望。", vi: "Cuối tuần ghé check-in chắc chắn sẽ không thất vọng." },
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
      { start: 1.2, end: 3.8, zh: "今天教大家做一道家常红烧肉。", vi: "Hôm nay mình dạy mọi người làm món thịt kho tàu kiểu gia đình." },
      { start: 4.1, end: 6.6, zh: "先把五花肉切成小块焯水。", vi: "Trước tiên thái thịt ba chỉ thành miếng nhỏ rồi chần qua nước sôi." },
      { start: 7.0, end: 9.6, zh: "小火慢炖四十分钟就好了。", vi: "Hầm lửa nhỏ bốn mươi phút là xong." },
    ],
  },
];

export type MarqueeItem = { han?: string; label: string; note?: string };

/** Weibo and Xigua are not supported by the app yet, so they carry a note instead of a claim. */
export const PLATFORM_ITEMS: MarqueeItem[] = [
  { han: "抖音", label: "Douyin" },
  { han: "哔哩哔哩", label: "Bilibili" },
  { han: "小红书", label: "Xiaohongshu" },
  { han: "快手", label: "Kuaishou" },
  { label: "TikTok" },
  { label: "YouTube" },
  { label: "MP4 / MKV trên máy" },
  { han: "微博", label: "Weibo", note: "chưa hỗ trợ" },
  { han: "西瓜视频", label: "Xigua", note: "chưa hỗ trợ" },
];

export const TECH_ITEMS: MarqueeItem[] = [
  { label: "HTTP Range × 6" },
  { label: "Chrome --user-data-dir" },
  { label: "FFmpeg + FFprobe" },
  { label: "VideOCR" },
  { label: "H.264 1,5–3,5 Mbps" },
  { label: "DeepSeek API" },
  { label: "Gemini Notebook" },
  { label: "yt-dlp" },
  { label: ".raw.srt → .vi.srt" },
  { label: "SHA256 auto-update" },
  { label: "Windows 10/11" },
];

export const METRICS = [
  { value: "15,4 s → 2,6 s", label: "tải 6 MB từ máy chủ giới hạn 0,4 MB/s mỗi kết nối, trong thử nghiệm của v1.6.2" },
  { value: "15–20 MB/phút", label: "Douyin chọn bản H.264 720p–1080p ở 1,5–3,5 Mbps, đủ nét cho OCR" },
  { value: "6 nền tảng", label: "Douyin, Bilibili, Xiaohongshu, Kuaishou, TikTok, YouTube, cùng file trên máy" },
  { value: "333 test", label: "test tự động (Python và giao diện) chạy lại trên mỗi bản build" },
];

export const PIPELINE_STEPS = [
  { title: "Link share / File local", body: "Nguyên đoạn share 抖音 · B站 · 小红书 · 快手, hoặc MP4/MKV trên máy.", icon: "link" },
  { title: "Chrome profile độc lập · Tải 6 kết nối", body: "--user-data-dir riêng, tải theo đoạn HTTP Range song song.", icon: "zap" },
  { title: "FFmpeg + VideOCR", body: "Cắt đúng vùng chữ, OCR ch · en · ja · ko · vi.", icon: "scan" },
  { title: "DeepSeek API / Gemini Notebook", body: "Dịch theo đoạn, giữ nguyên index và timestamp.", icon: "languages" },
  { title: "File .vi.srt chuẩn", body: "Chỉ lưu khi số block, index và timecode đều khớp.", icon: "file" },
] as const;

export const REQUIREMENTS = {
  required: ["Windows 10/11 x64", "VideOCR CLI", "API dịch (DeepSeek, OpenRouter…) hoặc Gemini Notebook"],
  recommended: ["Google Chrome (hoặc Edge) cho Douyin, Kuaishou, Notebook", "FFmpeg + FFprobe trong PATH", "Deno hoặc Node.js 22+ nếu tải YouTube"],
};

export const FAQS = [
  {
    q: "App có đụng tới Chrome hoặc Edge tôi đang dùng không?",
    a: "Không. App mở Google Chrome với profile riêng (--user-data-dir trong thư mục cấu hình), chạy song song với trình duyệt hằng ngày và không đọc hay sửa profile của bạn. Trình duyệt chỉ mở khi cần: dịch bằng Gemini Notebook, tải video cần trang web, hoặc khi bạn bấm đăng nhập.",
  },
  {
    q: "Video Douyin chỉ cho người đã đăng nhập xem thì sao?",
    a: "App dừng ngay thay vì chờ hết giờ, và hướng dẫn bấm “Mở Douyin trên Chrome” trong Thiết lập, đăng nhập một lần rồi thử lại.",
  },
  {
    q: "Có dùng được video đã có sẵn trên máy không?",
    a: "Có. Trong cửa sổ app, bấm Chọn video để mở thẳng MP4 hoặc MKV. File được dùng tại chỗ: không upload, không sao chép qua %TEMP%, và được hard-link vào project nếu cùng ổ đĩa.",
  },
  {
    q: "Timestamp có bị model dịch sửa không?",
    a: "Studio đối chiếu index và timestamp với SRT gốc. Nếu model chỉ đổi timing mà vẫn giữ đúng block, app khôi phục timestamp gốc; block thiếu hoặc dính nhau bị chặn trước khi lưu.",
  },
  {
    q: "Bản portable đã kèm VideOCR và FFmpeg chưa?",
    a: "Chưa. Bản portable gồm VietSub Studio và bộ tải yt-dlp. VideOCR, FFmpeg, Google Chrome (hoặc Edge) và tài khoản AI là phụ thuộc bên ngoài do bạn tự cài.",
  },
];
