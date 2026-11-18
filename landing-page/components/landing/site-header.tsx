"use client";

import { Download, GitBranch, Menu, X } from "lucide-react";
import Image from "next/image";
import { useEffect, useState } from "react";
import { RELEASE_URL, REPO_URL, VERSION } from "./data";

const NAV = [
  { href: "#demo", label: "Demo" },
  { href: "#pipeline", label: "Quy trình" },
  { href: "#features", label: "Tính năng" },
  { href: "#faq", label: "FAQ" },
];

export function SiteHeader() {
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 12);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <header className={`site-header ${scrolled ? "is-scrolled" : ""}`}>
      <div className="wrap header-row">
        <a href="#top" className="brand" aria-label="VietSub Studio, về đầu trang">
          <Image src="/avatar.jpg" width={32} height={32} alt="" className="brand-mark" />
          <span className="brand-name">VietSub Studio</span>
          <span className="brand-ver">v{VERSION}</span>
        </a>

        <nav className="nav-desktop" aria-label="Điều hướng chính">
          {NAV.map((item) => (
            <a key={item.href} href={item.href}>
              {item.label}
            </a>
          ))}
        </nav>

        <div className="header-actions">
          <a href={REPO_URL} target="_blank" rel="noreferrer" className="btn-ghost hide-sm">
            <GitBranch size={16} aria-hidden="true" /> GitHub
          </a>
          <a href={RELEASE_URL} target="_blank" rel="noreferrer" className="btn-sun-sm">
            <Download size={16} aria-hidden="true" /> Tải v{VERSION}
          </a>
          <button
            type="button"
            className="menu-btn"
            aria-expanded={open}
            aria-controls="mobile-nav"
            aria-label={open ? "Đóng menu" : "Mở menu"}
            onClick={() => setOpen((v) => !v)}
          >
            {open ? <X size={20} aria-hidden="true" /> : <Menu size={20} aria-hidden="true" />}
          </button>
        </div>
      </div>

      <nav id="mobile-nav" className="nav-mobile" aria-label="Điều hướng trên di động" hidden={!open}>
        {NAV.map((item) => (
          <a key={item.href} href={item.href} onClick={() => setOpen(false)}>
            {item.label}
          </a>
        ))}
        <a href={REPO_URL} target="_blank" rel="noreferrer" onClick={() => setOpen(false)}>
          GitHub
        </a>
      </nav>
    </header>
  );
}
