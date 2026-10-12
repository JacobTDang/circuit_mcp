"""Small PDFs built in memory, so the upload tests need no binary fixtures."""
from __future__ import annotations

from io import BytesIO

from pypdf import PdfWriter


def encrypted_pdf(user_password: str) -> bytes:
    """One blank page. An empty user password locks it only against editing."""
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt(user_password=user_password, owner_password="owner", algorithm="RC4-128")
    stream = BytesIO()
    writer.write(stream)
    return stream.getvalue()


def text_pdf(lines: list[str]) -> bytes:
    """A minimal PDF with one line of text on each page."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for line in lines:
        stream = b"BT /F1 12 Tf 72 720 Td (%s) Tj ET" % line.encode()
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
        objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                       b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % len(objects))
        kids.append(len(objects))
    objects[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % kid for kid in kids), len(kids))
    body, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, content in enumerate(objects, 1):
        offsets.append(len(body))
        body += b"%d 0 obj\n%s\nendobj\n" % (number, content)
    xref = len(body)
    body += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    body += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    body += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(body)
