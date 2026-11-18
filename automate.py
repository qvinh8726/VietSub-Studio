"""Command-line entry point for the same workflow used by the local web app."""

import argparse
import sys

from app import acquire_instance_lock, release_instance_lock, run_single_workflow


def configure_console_encoding():
    # A redirected stdout/stderr on Windows uses the ANSI code page, which cannot
    # encode Vietnamese. UTF-8 keeps every message readable whatever the code page.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (OSError, ValueError):
            pass


def main():
    configure_console_encoding()
    parser = argparse.ArgumentParser(
        description="Tải một video Douyin, OCR phụ đề và dịch sang tiếng Việt."
    )
    parser.add_argument("-u", "--url", required=True, help="Link video Douyin")
    parser.add_argument(
        "-l", "--lang", default="ch", help="Ngôn ngữ OCR: ch, en, ja, ko hoặc vi"
    )
    parser.add_argument(
        "-n", "--name", default="", help="Tên dùng chung cho video và hai file phụ đề"
    )
    args = parser.parse_args()

    if not acquire_instance_lock():
        print("Lỗi: VietSub Studio đang chạy ở một tiến trình khác.", file=sys.stderr)
        return 1
    try:
        try:
            result = run_single_workflow(args.url, args.lang, args.name)
        except (RuntimeError, ValueError) as error:
            print(f"Lỗi: {error}", file=sys.stderr)
            return 1
    finally:
        release_instance_lock()

    if result["status"] != "success":
        print(f"Lỗi: {result['error']}", file=sys.stderr)
        return 1

    print(f"Video gốc: {result['result']['video']}")
    print(f"Phụ đề OCR: {result['result']['raw_srt']}")
    print(f"Phụ đề dịch: {result['result']['translated_srt']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
