"""生成 samples/sample_doc.pdf：6 页，第 2、5 页无内容，第 6 页只有空白字符，第 3 页超过 7000 字节用于触发分块。

只用标准库手写 PDF，免得为一份测试语料引入额外依赖。
用法：python samples/make_sample_pdf.py
"""

from pathlib import Path

OUTPUT = Path(__file__).resolve().parent / "sample_doc.pdf"

FONT_SIZE = 12
LINE_HEIGHT = 16
MARGIN_X = 16
TOP_Y = 760
PAGE_SIZE = "0 0 595 842"


def _escape(text: str) -> str:
    out: list[str] = []
    for char in text:
        if char in "\\()":
            out.append("\\" + char)
        elif 32 <= ord(char) < 127:
            out.append(char)
        elif ord(char) < 256:
            out.append("\\%03o" % ord(char))  # Latin-1 区间与 WinAnsi 同码
        else:
            out.append("?")
    return "".join(out)


def _content_stream(lines: list[str]) -> bytes:
    if not lines:
        return b""
    parts = [f"BT /F1 {FONT_SIZE} Tf {MARGIN_X} {TOP_Y} Td"]
    for index, line in enumerate(lines):
        if index:
            parts.append(f"0 -{LINE_HEIGHT} Td")
        parts.append(f"({_escape(line)}) Tj")
    parts.append("ET")
    return " ".join(parts).encode("latin-1")


def build_pdf(pages: list[list[str]]) -> bytes:
    kids = " ".join(f"{4 + 2 * index} 0 R" for index in range(len(pages)))
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    for index, lines in enumerate(pages):
        stream = _content_stream(lines)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [{PAGE_SIZE}] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * index} 0 R >>".encode()
        )
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))

    body = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"

    xref_position = len(body)
    size = len(objects) + 1
    body += f"xref\n0 {size}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        body += b"%010d 00000 n \n" % offset
    body += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_position}\n%%EOF\n".encode()
    return bytes(body)


def sample_pages() -> list[list[str]]:
    unit = "The quick brown fox jumps over the lazy dog 0123456789. "
    long_line = (unit * 4)[:200]
    return [
        [
            "docmind sample document",
            "A tiny hand-written PDF used to exercise /ingest and /ingest/preview.",
            "Accented text survives WinAnsi: cafe -> " + chr(233) + chr(239) + chr(252) + chr(231) + chr(177) + ".",
            "Page 2 and page 5 carry no text; page 6 is whitespace only. Page 3 is long enough to split.",
        ],
        [],
        [f"chunk probe {index:02d} :: {long_line}" for index in range(45)],
        [
            "Idempotency note",
            "Re-ingesting this file must return status skipped because the sha256 is in samples/manifest.json.",
            "Deleting that entry forces a real re-write.",
        ],
        [],
        ["   ", "\t"],
    ]


if __name__ == "__main__":
    data = build_pdf(sample_pages())
    OUTPUT.write_bytes(data)
    print(f"{OUTPUT}  {len(data)} bytes  pages={len(sample_pages())}")
