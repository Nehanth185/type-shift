"""Turn an uploaded PDF / Markdown / text file into pages and searchable chunks.

Page numbers:
  PDF       the printed page label if the PDF has one (e.g. a manual excerpt that starts at p.14), else 1, 2, 3...
  MD / TXT  a line like  <!-- page: 14 -->  starts page 14; a form feed (\\f) starts the next page;
            with neither, the whole file is page 1.
Scanned PDF pages (no text layer) are OCR'd with the pdftoppm + tesseract CLIs when both are installed, else rejected.
"""
import io, os, re

PAGE_MARK = re.compile(r"^\s*<!--\s*page\s*:?\s*([\w.-]+)\s*-->\s*$", re.I | re.M)
ALLOWED = {".pdf": "pdf", ".md": "md", ".markdown": "md", ".txt": "txt"}
CHUNK_WORDS, OVERLAP_WORDS = 110, 25


class IngestError(ValueError):
    pass


def kind_for(filename):
    return ALLOWED.get(os.path.splitext(filename or "")[1].lower())


def parse(filename, data: bytes):
    """-> (title, kind, [(page_label, text)])"""
    kind = kind_for(filename)
    if not kind:
        raise IngestError("only .pdf, .md and .txt files are supported")
    stem = os.path.splitext(os.path.basename(filename))[0].replace("_", " ").replace("-", " ").strip()
    if kind == "pdf":
        title, pages = _parse_pdf(data)
    else:
        text = data.decode("utf-8", "replace").replace("\r\n", "\n")
        title, pages = None, _split_text_pages(text)
        m = re.search(r"^\s*#\s+(.+?)\s*#*\s*$", text, re.M)
        if m: title = m.group(1)
    pages = [(p, t) for p, t in pages if t.strip()]
    if not pages:
        raise IngestError("no text found in this file (scanned PDF? OCR needs pdftoppm + tesseract on the server)")
    return (title or stem or "Untitled")[:120], kind, pages


def _split_text_pages(text):
    marks = list(PAGE_MARK.finditer(text))
    if marks:
        pages = []
        head = text[:marks[0].start()]  # title + front matter: belongs to the first marked page
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            pages.append((m.group(1), (head if i == 0 else "") + text[m.end():end]))
        return pages
    parts = text.split("\f")
    return [(str(i + 1), p) for i, p in enumerate(parts)]


def _ocr_page(data, pageno):
    """OCR one PDF page with pdftoppm + tesseract CLIs; '' if unavailable."""
    import shutil, subprocess, tempfile
    if not (shutil.which("pdftoppm") and shutil.which("tesseract")):
        return ""
    try:
        with tempfile.TemporaryDirectory() as d:
            open(d + "/in.pdf", "wb").write(data)
            subprocess.run(["pdftoppm", "-r", "200", "-f", str(pageno), "-l", str(pageno),
                            "-png", d + "/in.pdf", d + "/p"], check=True, timeout=60,
                           capture_output=True)
            png = sorted(f for f in os.listdir(d) if f.endswith(".png"))[0]
            r = subprocess.run(["tesseract", d + "/" + png, "stdout"], timeout=90,
                               capture_output=True, text=True)
            return r.stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def _parse_pdf(data):
    try:
        from pypdf import PdfReader
    except ImportError as e:  # pragma: no cover
        raise IngestError("PDF support needs `pip install pypdf`") from e
    try:
        r = PdfReader(io.BytesIO(data))
        labels = list(r.page_labels) if r.page_labels else []
    except Exception as e:  # noqa: BLE001
        raise IngestError(f"could not read PDF: {str(e)[:120]}") from None
    pages = []
    for i, pg in enumerate(r.pages):
        try:
            t = pg.extract_text() or ""
        except Exception:  # noqa: BLE001
            t = ""
        label = labels[i] if i < len(labels) and labels[i] else str(i + 1)
        if len(t.strip()) < 20:  # likely scanned page: try OCR
            t = _ocr_page(data, i + 1) or t
        pages.append((label, t))
    title = None
    try:
        if r.metadata and r.metadata.title: title = str(r.metadata.title)
    except Exception:  # noqa: BLE001
        pass
    return title, pages


def clean(text):
    """Markdown -> plain sentences (keeps numbers, units, list numbering)."""
    text = PAGE_MARK.sub("", text)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"^\s*#+\s*", "", text, flags=re.M)
    text = re.sub(r"[*_`]{1,3}", "", text)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*\|?[-: |]+\|\s*$", "", text, flags=re.M)  # table rules
    text = text.replace("|", " ")
    return text


def chunk_pages(pages):
    """-> [(page_label, chunk_text)]: ~110-word windows inside each page, 25-word overlap. Never crosses a page,
    so every chunk has exactly one page to cite."""
    out = []
    for label, raw in pages:
        lines = [re.sub(r"[ \t]+", " ", l).strip() for l in clean(raw).split("\n")]
        words = " \n ".join(l for l in lines if l).split(" ")
        words = [w for w in words if w]
        if not words: continue
        i = 0
        while i < len(words):
            piece = " ".join(words[i:i + CHUNK_WORDS]).replace(" \n ", "\n").replace("\n ", "\n").strip()
            if piece.replace("\n", "").strip(): out.append((label, piece))
            if i + CHUNK_WORDS >= len(words): break
            i += CHUNK_WORDS - OVERLAP_WORDS
    return out
