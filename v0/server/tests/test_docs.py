"""POST /docs, GET /docs, DELETE /docs/{doc_id}"""
from conftest import ask
from minipdf import write_pdf

MD = b"""# Boiler B-7 Notes (fictional)

<!-- page: 3 -->
Normal water level is half way up the sight glass.

<!-- page: 4 -->
1. Shut the gas valve.
2. Wait 10 minutes.
3. Open the blowdown valve.
"""


def upload(client, name, data, ws="ws1", ctype="application/octet-stream"):
    return client.post("/docs", data={"workspace_id": ws}, files={"file": (name, data, ctype)})


def test_upload_markdown_with_page_markers(fresh):
    r = upload(fresh, "boiler.md", MD)
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d) == {"doc_id", "title", "pages", "chunks"}
    assert d["title"] == "Boiler B-7 Notes (fictional)" and d["pages"] == 2 and d["chunks"] >= 2
    b = ask(fresh, "What is the normal water level in the sight glass?", ws="ws1")
    assert b["sources"][0]["page"] == "3" and b["sources"][0]["doc_id"] == d["doc_id"]


def test_upload_txt_form_feed_pages(fresh):
    d = upload(fresh, "shift notes.txt", b"Forklift charger is in bay 2.\fForklift keys hang in the office.").json()
    assert d["pages"] == 2 and d["title"] == "shift notes"
    b = ask(fresh, "Where do the forklift keys hang?", ws="ws1")
    assert b["sources"][0]["page"] == "2"


def test_upload_pdf_uses_printed_page_labels(fresh):
    pdf = write_pdf(None, [["Compressor C-9 (fictional)", "Oil level must be between the two marks."],
                           ["Drain the receiver tank daily."]], title="Compressor C-9 Manual", page_labels=[31, 32])
    d = upload(fresh, "c9.pdf", pdf, ctype="application/pdf").json()
    assert d["title"] == "Compressor C-9 Manual" and d["pages"] == 2
    b = ask(fresh, "How often do I drain the receiver tank?", ws="ws1")
    assert b["sources"][0]["page"] == "32" and b["sources"][0]["title"] == "Compressor C-9 Manual"
    assert "p.32" in b["answer_hud"][-1]


def test_reject_bad_type_missing_workspace_and_unreadable_pdf(fresh):
    assert upload(fresh, "photo.jpg", b"\xff\xd8xx").status_code == 415
    assert fresh.post("/docs", files={"file": ("a.md", b"# hi\ntext")}).status_code == 422
    assert upload(fresh, "broken.pdf", b"%PDF-1.4 not really").status_code == 422
    scanned = write_pdf(None, [[]], title="scan")  # a page with no text layer
    r = upload(fresh, "scan.pdf", scanned)
    assert r.status_code == 422 and "OCR" in r.json()["detail"]
    assert upload(fresh, "empty.md", b"   \n").status_code == 422


def test_list_is_scoped_to_workspace(fresh):
    upload(fresh, "a.md", b"# A\nalpha text here", ws="ws1")
    upload(fresh, "b.md", b"# B\nbravo text here", ws="ws2")
    l1 = fresh.get("/docs", params={"workspace_id": "ws1"}).json()
    assert [d["title"] for d in l1] == ["A"]
    assert {"doc_id", "title", "pages", "chunks", "created_at"} <= set(l1[0])
    assert fresh.get("/docs", params={"workspace_id": "nobody"}).json() == []
    assert fresh.get("/docs").status_code == 422


def test_delete_doc_removes_it_from_answers(fresh):
    d = upload(fresh, "boiler.md", MD).json()
    assert ask(fresh, "What is the normal water level in the sight glass?", ws="ws1")["sources"]
    assert fresh.delete(f"/docs/{d['doc_id']}").json() == {"deleted": d["doc_id"]}
    assert fresh.delete(f"/docs/{d['doc_id']}").status_code == 404
    assert fresh.get("/docs", params={"workspace_id": "ws1"}).json() == []
    b = ask(fresh, "What is the normal water level in the sight glass?", ws="ws1")
    assert b["sources"] == [] and b["answer_hud"][0] == "Not in your docs"


def test_demo_workspace_seeds_three_docs(demo):
    docs = demo.get("/docs", params={"workspace_id": "demo"}).json()
    assert sorted(d["title"] for d in docs) == ["CP-450 Pump Manual (fictional excerpt)",
                                                "Unit IV Pump Quick Guide (fictional)", "glasses-v0 bench notes"]
    pump = next(d for d in docs if d["kind"] == "pdf")
    assert pump["pages"] == 3
