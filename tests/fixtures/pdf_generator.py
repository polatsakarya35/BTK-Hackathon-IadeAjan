"""Harici bağımlılık olmadan minimal geçerli PDF üretir."""

from __future__ import annotations

from pathlib import Path


def write_minimal_pdf(path: Path, *, title: str = "GCB Test") -> Path:
    """Tek sayfalık minimal PDF 1.4 — belge pipeline dosya adı testleri için."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe = title.replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({safe}) Tj ET"
    stream_bytes = stream.encode("latin-1", errors="replace")
    objects: list[bytes] = []

    def add_obj(body: str) -> int:
        objects.append(body.encode("latin-1", errors="replace"))
        return len(objects)

    add_obj("<< /Type /Catalog /Pages 2 0 R >>")
    add_obj("<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    add_obj(
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
    )
    add_obj(f"<< /Length {len(stream_bytes)} >>\nstream\n{stream}\nendstream")
    add_obj("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    pdf = b"%PDF-1.4\n"
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"

    xref_start = len(pdf)
    pdf += f"xref\n0 {len(objects) + 1}\n".encode()
    pdf += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        pdf += f"{off:010d} 00000 n \n".encode()
    pdf += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_start}\n%%EOF\n"
    ).encode()
    path.write_bytes(pdf)
    return path
