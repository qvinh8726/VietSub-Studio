"""Command-line entry point for the same workflow used by the local web app."""

import argparse
import sys

from app import progress_snapshot, queue_workflow


def main():
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

    try:
        thread = queue_workflow(args.url, args.lang, args.name)
    except (RuntimeError, ValueError) as error:
        print(f"Lỗi: {error}", file=sys.stderr)
        return 1

    thread.join()
    result = progress_snapshot()
    if result["status"] != "success":
        print(f"Lỗi: {result['error']}", file=sys.stderr)
        return 1

    print(f"Video gốc: {result['result']['video']}")
    print(f"Phụ đề OCR: {result['result']['raw_srt']}")
    print(f"Phụ đề dịch: {result['result']['translated_srt']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
