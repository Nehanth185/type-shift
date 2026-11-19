"""Tiny dependency-free PDF writer (text only, Helvetica, ASCII). Used to build the sample manual PDF and the
test fixtures, so no reportlab/fpdf install is needed.

  write_pdf(path_or_None, pages=[["line", ...], ...], title="...", page_labels=[14, 21, 22]) -> bytes
page_labels: printed page numbers (sets the PDF /PageLabels so readers and pypdf show "14", not "1").
CLI: python tools/minipdf.py source.md out.pdf   (splits on <!-- page: N --> markers, strips markdown)
"""
import re, sys


def _esc(s):
    s = s.encode("ascii", "replace").decode()
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _wrap(line, width=88):
    out, cur = [], ""
    for w in line.split(" "):
        if cur and len(cur) + 1 + len(w) > width:
            out.append(cur); cur = "   " + w
        else:
            cur = f"{cur} {w}" if cur else w
    return out + [cur]


def write_pdf(path, pages, title="", page_labels=None):
    objs = []  # index i -> object number i+1

    def add(body):
        objs.append(body); return len(objs)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids, pages_id = [], len(pages) * 2 + 2  # reserve: content+page per page, then Pages
    for lines in pages:
        y, ops = 770, ["BT /F1 11 Tf 14 TL 56 770 Td"]
        for ln in lines:
            for piece in _wrap(ln):
                ops.append(f"({_esc(piece)}) Tj T*")
                y -= 14
        ops.append("ET")
        stream = "\n".join(ops).encode()
        c = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        p = add(f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] /Contents {c} 0 R "
                f"/Resources << /Font << /F1 {font} 0 R >> >> >>".encode())
        page_ids.append(p)
    assert add(f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] /Count {len(page_ids)} >>".encode()) == pages_id
    labels = ""
    if page_labels:
        nums = " ".join(f"{i} << /S /D /St {int(n)} >>" for i, n in enumerate(page_labels))
        labels = f" /PageLabels << /Nums [{nums}] >>"
    cat = add(f"<< /Type /Catalog /Pages {pages_id} 0 R{labels} >>".encode())
    info = add(f"<< /Title ({_esc(title)}) /Producer (minipdf) >>".encode())
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objs):
        offs.append(len(out))
        out += f"{i + 1} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root {cat} 0 R /Info {info} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    if path:
        open(path, "wb").write(bytes(out))
    return bytes(out)


def md_to_pdf(src, dst):
    text = open(src, encoding="utf-8").read()
    title = re.search(r"^#\s+(.+)$", text, re.M).group(1).strip()
    marks = list(re.finditer(r"^<!--\s*page\s*:?\s*(\w+)\s*-->\s*$", text, re.M))
    head = text[:marks[0].start()]
    pages, labels = [], []
    for i, m in enumerate(marks):
        body = (head if i == 0 else "") + text[m.end():marks[i + 1].start() if i + 1 < len(marks) else len(text)]
        lines = [re.sub(r"^#+\s*|[*_`]", "", l).rstrip() for l in body.strip().split("\n")]
        pages.append(lines); labels.append(int(m.group(1)))
    write_pdf(dst, pages, title, labels)


if __name__ == "__main__":
    md_to_pdf(sys.argv[1], sys.argv[2])
