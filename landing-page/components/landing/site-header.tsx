"use client";

import { Clapperboard, Download, GitBranch, Menu, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { RELEASE_URL, REPO_URL, VERSION } from "./data";

const NAV = [
  { href: "#demo", label: "Studio 3-trong-1" },
  { href: "#pipeline", label: "Hành trình" },
  { href: "#features", label: "Điểm khác biệt" },
  { href: "#faq", label: "Hỏi & đáp" },
];

export function SiteHeader() {
  const [open, setOpen] = useState(false);
  const toggle = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        toggle.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    const media = matchMedia("(min-width: 1000px)");
    const close = () => {
      if (media.matches) setOpen(false);
    };
    media.addEventListener("change", close);
    return () => {
      window.removeEventListener("keydown", onKey);
      media.removeEventListener("change", close);
    };
  }, [open]);
  return (
    <header className="site-header">
      <div className="wrap header-row">
        <a href="#top" className="brand" title="Về đầu trang">
          <span className="brand-symbol">
            <Clapperboard size={21} strokeWidth={1.7} aria-hidden="true" />
          </span>
          <span>
            VietSub<span className="brand-light"> Studio</span>
          </span>
          <span className="brand-ver">2.1</span>
        </a>
        <nav className="nav-desktop" aria-label="Điều hướng chính">
          {NAV.map((item) => (
            <a key={item.href} href={item.href}>
              {item.label}
            </a>
          ))}
        </nav>
        <div className="header-actions">
          <a
            href={REPO_URL}
            target="_blank"
            rel="noreferrer"
            className="github-link"
            aria-label="Mở mã nguồn GitHub"
          >
            <GitBranch size={18} aria-hidden="true" />
          </a>
          <a
            href={RELEASE_URL}
            target="_blank"
            rel="noreferrer"
            className="btn-sun-sm"
          >
            <Download size={15} aria-hidden="true" /> Tải v{VERSION}
          </a>
          <button
            ref={toggle}
            type="button"
            className="menu-btn"
            aria-expanded={open}
            aria-controls="mobile-nav"
            aria-label={open ? "Đóng menu" : "Mở menu"}
            onClick={() => setOpen((v) => !v)}
          >
            {open ? (
              <X size={21} aria-hidden="true" />
            ) : (
              <Menu size={21} aria-hidden="true" />
            )}
          </button>
        </div>
      </div>
      <nav
        id="mobile-nav"
        className="nav-mobile"
        aria-label="Điều hướng trên di động"
        hidden={!open}
      >
        {NAV.map((item) => (
          <a key={item.href} href={item.href} onClick={() => setOpen(false)}>
            {item.label}
          </a>
        ))}
      </nav>
    </header>
  );
}
