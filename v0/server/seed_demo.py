"""Load sample_workspace/ into workspace "demo" (skips if it already has docs).

  python seed_demo.py                 # uses GLASSES_DB (default data/glasses.db)
  SEED_DEMO=1 python server.py        # same thing at server start
  SEED_DEMO_DOCS=1 uvicorn server:app # hosted demo (Render): load only if the whole DB has no docs yet (fresh disk)

Docs (see sample_workspace/README.md): a fictional CP-450 pump manual excerpt (PDF, pages 14, 21, 22),
a fictional IV pump quick guide, and our own glasses-v0 bench notes.
"""
import glob, os

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(HERE, "sample_workspace")
WORKSPACE = "demo"


def seed(store, workspace_id=WORKSPACE, force=False):
    import workapi
    if store.list_docs(workspace_id) and not force:
        return []
    out = []
    for path in sorted(glob.glob(os.path.join(SAMPLE, "*"))):
        name = os.path.basename(path)
        if name.lower() == "readme.md" or os.path.isdir(path): continue
        out.append(workapi.add_document(workspace_id, name, open(path, "rb").read()))
    return out


def seed_if_empty(store, workspace_id=WORKSPACE):
    """Hosted demo (G37): Render's free disk is wiped on every sleep/restart, so the server starts on an empty DB.
    Load sample_workspace/ only when the DB holds no docs at all; never touches a DB that already has any."""
    if store.q("SELECT COUNT(*) AS n FROM docs")[0]["n"]:
        return []
    return seed(store, workspace_id)


if __name__ == "__main__":
    import workapi
    from llm import MockProvider
    st = workapi.init(MockProvider())
    for d in seed(st) or ["(workspace 'demo' already has docs)"]:
        print(d)
