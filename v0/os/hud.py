"""HUD cards. Every string the glasses show (title, each line, why) is at most 40 characters and 7 words, the same
rule as the work API (grounded.clamp_line). A card body is at most 3 lines."""
from grounded import HUD_CHARS, HUD_LINES, HUD_WORDS, clamp_line, wrap  # noqa: F401


def card(app, title, lines, speech="", why=None, why_full=None, kind="app", **extra):
    body = [clamp_line(str(l)) for l in (lines or []) if l is not None and str(l).strip()][:HUD_LINES] or ["Ready"]
    c = {"app": app, "kind": kind, "title": clamp_line(title or app), "lines": body, "speech": speech or ""}
    if why:
        c["why"] = clamp_line(why)
        c["why_full"] = why_full or why
    c.update(extra)
    return c


def violations(c):
    """List of HUD-rule breaks in a card (empty = OK). Used by the tests."""
    bad = []
    shown = [c.get("title", "")] + list(c.get("lines", [])) + ([c["why"]] if c.get("why") else [])
    if not 1 <= len(c.get("lines", [])) <= HUD_LINES: bad.append(f"{len(c.get('lines', []))} lines")
    for s in shown:
        if len(s) > HUD_CHARS: bad.append(f"too long: {s!r}")
        if len(s.split()) > HUD_WORDS: bad.append(f"too many words: {s!r}")
    return bad
