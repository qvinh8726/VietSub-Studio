import type { Metadata, Viewport } from "next";
import { Be_Vietnam_Pro, JetBrains_Mono, Noto_Sans_SC } from "next/font/google";
import "./globals.css";

// Headings and body: designed for Vietnamese diacritics.
const sansFont = Be_Vietnam_Pro({
  subsets: ["latin", "vietnamese"],
  weight: ["400", "500", "600", "700", "800"],
  variable: "--font-sans",
  display: "swap",
});

// Chinese text in the sandbox and marquee. Not preloaded: the browser fetches only the slices the page uses.
const cjkFont = Noto_Sans_SC({
  subsets: ["latin"],
  variable: "--font-cjk",
  display: "swap",
  preload: false,
});

// SRT blocks and timecodes.
const monoFont = JetBrains_Mono({
  subsets: ["latin", "vietnamese"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "VietSub Studio v1.6.2 — OCR và dịch phụ đề video Trung sang tiếng Việt",
  description:
    "Công cụ Windows mã nguồn mở: dán link share Douyin, Bilibili, Xiaohongshu, Kuaishou, TikTok, YouTube hoặc mở file trên máy, OCR phụ đề cứng bằng VideOCR, dịch sang tiếng Việt và giữ nguyên timestamp SRT. v1.6.2 tải 6 kết nối song song.",
};

export const viewport: Viewport = {
  themeColor: "#07110e",
  colorScheme: "dark",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="vi">
      <body className={`${sansFont.variable} ${cjkFont.variable} ${monoFont.variable}`}>{children}</body>
    </html>
  );
}
