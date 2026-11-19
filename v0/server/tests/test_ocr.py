import shutil, subprocess, pytest
from PIL import Image, ImageDraw, ImageFont
import io, ingest

@pytest.mark.skipif(not shutil.which("tesseract"), reason="no tesseract")
def test_scanned_pdf_ocr():
    im = Image.new("RGB", (1200, 400), "white")
    d = ImageDraw.Draw(im)
    try: f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 60)
    except Exception: f = ImageFont.load_default()
    d.text((40, 150), "Torque spec 45 Nm", fill="black", font=f)
    b = io.BytesIO(); im.save(b, "PDF")
    title, pages = ingest._parse_pdf(b.getvalue())
    assert "torque" in pages[0][1].lower()
