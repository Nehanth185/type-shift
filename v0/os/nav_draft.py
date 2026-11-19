"""Draft a fault tree from a manual page (never loaded until a person reviews it and sets reviewed=true).

  python nav_draft.py PDF PAGE --model "Sta-Rite SX218" --url URL [--llm]     -> trees/drafts/<id>.draft.json
Table parser first (deterministic, mock-safe). --llm also asks the configured real model (NVIDIA NIM via
NVIDIA_API_KEY) for a second draft, saved beside it, with every quote marked found / not found on the page.
"""
import argparse, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(os.path.dirname(HERE), "server"))
import boot  # noqa: E402
nt = boot.load().nav_tree

ap = argparse.ArgumentParser()
ap.add_argument("pdf"); ap.add_argument("page", type=int)
ap.add_argument("--model", required=True); ap.add_argument("--url", default=""); ap.add_argument("--title", default="")
ap.add_argument("--llm", action="store_true")
a = ap.parse_args()
text = subprocess.run(["pdftotext", "-layout", "-f", str(a.page), "-l", str(a.page), a.pdf, "-"],
                      capture_output=True, text=True, check=True).stdout
parsed = nt.parse_x_matrix(text)
out = os.path.join(nt.TREES, "drafts")
os.makedirs(out, exist_ok=True)
if parsed:
    d = nt.draft_from_matrix(parsed, doc_title=a.title or os.path.basename(a.pdf), page=str(a.page), url=a.url, model=a.model)
    p = os.path.join(out, f"{d['id']}.draft.json")
    json.dump(d, open(p, "w"), indent=1, ensure_ascii=False)
    print("table draft:", p, len(d["symptoms"]), "symptoms,", sum(len(s["causes"]) for s in d["symptoms"]), "cause links")
else:
    print("no X-matrix table found on that page")
if a.llm:
    from llm import make_provider
    r = nt.llm_draft(make_provider(), text)
    if r is None:
        print("LLM draft: skipped (mock provider / no key / model error)", nt.LAST_ERROR)
    else:
        p = os.path.join(out, f"{nt.re.sub(r'[^a-z0-9]+', '-', a.model.lower()).strip('-')}.llm-draft.json")
        r.update({"reviewed": False, "drafted_by": "LLM (NIM)", "source": {"page": str(a.page), "url": a.url}})
        json.dump(r, open(p, "w"), indent=1, ensure_ascii=False)
        qs = [c.get("quote_on_page") for s in r.get("symptoms", []) for c in s.get("causes", [])]
        print("LLM draft:", p, len(r.get("symptoms", [])), "symptoms; cause quotes found on page:", sum(map(bool, qs)), "/", len(qs))
