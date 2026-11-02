"""Markdown → PDF（交付材料清单 §4.1：书面报告为 PDF 正文 + Markdown 源，PDF 文字可选中）。

用 python-markdown 转 HTML，再用本机 Chrome 无头模式打印成 PDF；不需要联网。
用法：python3 tools/md2pdf.py <输入.md> <输出.pdf>
"""
from __future__ import annotations

import html
import subprocess
import sys
import tempfile
from pathlib import Path

import markdown

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
]

CSS = """
@page { size: A4; margin: 16mm 14mm; }
body { font-family: "PingFang SC", "Noto Sans SC", "Microsoft YaHei", sans-serif; font-size: 10.5pt; line-height: 1.6; color: #14213D; }
h1 { font-size: 19pt; border-bottom: 2px solid #14213D; padding-bottom: 4px; }
h2 { font-size: 14pt; margin-top: 18px; border-bottom: 1px solid #ccc; padding-bottom: 2px; }
h3 { font-size: 12pt; margin-top: 14px; }
table { border-collapse: collapse; width: 100%; margin: 8px 0 12px; font-size: 8.5pt; page-break-inside: auto; }
tr { page-break-inside: avoid; }
th, td { border: 1px solid #bbb; padding: 3px 5px; vertical-align: top; text-align: left; word-break: break-word; }
th { background: #EFEBE1; }
code { font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 8.5pt; background: #f4f2ec; padding: 0 2px; }
pre { background: #f4f2ec; padding: 8px; white-space: pre-wrap; word-break: break-all; font-size: 8.5pt; }
blockquote { color: #555; border-left: 3px solid #ccc; margin: 6px 0; padding-left: 10px; }
"""


def main(src: Path, dst: Path) -> int:
    body = markdown.markdown(src.read_text(encoding="utf-8"), extensions=["tables", "fenced_code", "sane_lists"])
    title = html.escape(src.stem)
    page = f"<!doctype html><html lang='zh'><head><meta charset='utf-8'><title>{title}</title><style>{CSS}</style></head><body>{body}</body></html>"
    chrome = next((c for c in CHROME_CANDIDATES if Path(c).exists()), None)
    if chrome is None:
        print("未找到 Chrome / Chromium", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        h = Path(tmp) / "doc.html"
        h.write_text(page, encoding="utf-8")
        dst.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                            f"--print-to-pdf={dst.resolve()}", h.as_uri()], capture_output=True, text=True, timeout=180)
        if r.returncode != 0 or not dst.exists():
            print(r.stderr[-2000:], file=sys.stderr)
            return 1
    print(f"{dst}（{dst.stat().st_size // 1024} KB）")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
