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
  title: `VietSub Studio v${VERSION} — Một Studio, muôn cách tỏa sáng`,
  description: `VietSub Studio v${VERSION}: Creator Workbench 3-trong-1 với giao diện Neutral Studio Dark, Từ điển Glossary, Trình sửa SRT trực tiếp, phụ đề động ASS, Hồ sơ Kênh + Auto Pipeline, Publish Hub v2 (Assisted Upload + YouTube API v3) và cập nhật ký số Ed25519 trên Windows.`,
  openGraph: {
    title: `VietSub Studio v${VERSION} — 3-in-1 Creator Workbench`,
    description:
      "VietSub · Auto Edit · Đăng đa nền tảng. Giữ mạch sáng tạo, bớt những bước rời rạc.",
    type: "website",
    locale: "vi_VN",
  },
};

export const viewport: Viewport = {
  themeColor: "#07110e",
  colorScheme: "dark",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="vi">
      <body
        className={`${sansFont.variable} ${cjkFont.variable} ${monoFont.variable}`}
      >
        {children}
      </body>
    </html>
  );
}
