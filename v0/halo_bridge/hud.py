"""Shape answer text for Halo's 256x256 round display (16px font ~14 chars/line, 4 lines). [EST] sizes, unmeasured."""
import textwrap
LINE_W, LINES = 14, 4
def paginate(text, width=LINE_W, lines=LINES):
    words = []
    for w in text.split():
        while len(w) > width:
            words.append(w[:width-1] + "-"); w = w[width-1:]
        words.append(w)
    rows, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            rows.append(cur); cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur: rows.append(cur)
    return ["\n".join(rows[i:i+lines]) for i in range(0, len(rows), lines)] or [""]
