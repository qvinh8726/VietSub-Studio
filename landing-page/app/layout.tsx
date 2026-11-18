import type { Metadata, Viewport } from "next";
import { Be_Vietnam_Pro, JetBrains_Mono, Noto_Sans_SC } from "next/font/google";
import { VERSION } from "../components/landing/data";
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
  title: `VietSub Studio v${VERSION} — OCR và dịch phụ đề video Trung sang tiếng Việt`,
  description:
    `VietSub Studio v${VERSION}: nhập nhiều link hoặc MP4/MKV, OCR và dịch phụ đề sang tiếng Việt. Chrome headless, dọn video giữ SRT và publish pack AI tùy chọn.`,
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
