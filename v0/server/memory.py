"""Personal memory rules: voice commands that write memory, and "ready cards" built from it.

Memory is per worker and holds short text facts only (questions asked, guides started/finished, notes, the
current context). No photos, no audio. The worker can list it, delete one item, or forget everything; there is
deliberately no endpoint that lets an employer read it.

Kinds: ask | guide | job_done | note | context
Ready card (v1 = simple rules, not a model):
  unfinished      open notes + guides not finished, for this label (all labels at start of shift)
  last_answer     the HUD of the last question asked at this label
  usual_next_step resume an unfinished guide > an open note > the guide you start most here > the question
                  you ask most here
"""
import os, re, time
from collections import Counter

from grounded import clamp_line, short_title

CONTEXT_TTL_S = float(os.environ.get("CONTEXT_TTL_HOURS", "8")) * 3600

NOTE_RE = re.compile(r"^\s*(?:note|remember|remind me|reminder)\b(?:\s+(?:that|to))?\s*[:,\-]?\s*(.+)$", re.I)
DONE_RE = re.compile(r"^\s*(?:(?:i'?m\s+)?(?:done|finished|completed?)\b[\s,.!]*(?:log (?:it|that)\b)?|log (?:it|that)\b)"
                     r"[\s,.!]*(anyway)?[\s.!]*$", re.I)
DONE_CONFIRM_S = 120   # a second "done, log it" within this many seconds confirms logging an unfinished guide
_DONE_PENDING = {}     # worker_id -> time we asked "log it anyway?" (single process, like the rate limiter)


def done_pending(worker_id):
    t = _DONE_PENDING.get(worker_id)
    return bool(t and time.time() - t <= DONE_CONFIRM_S)
FORGET_RE = re.compile(r"^\s*forget (?:that|it|the last (?:one|thing))\b", re.I)
ABOUT_RE = re.compile(r"what do you (?:know|remember) about me", re.I)


def mmdd(ts):
    t = time.localtime(ts)
    return f"{t.tm_mon}/{t.tm_mday}"


def public(item):
    return {"id": item["id"], "kind": item["kind"], "text": item["text"], "created_at": item["created_at"],
            "label": item["meta"].get("label") or None}


def current_label(store, worker_id):
    for it in store.memory(worker_id, kinds={"context"}):
        if time.time() - it["ts"] <= CONTEXT_TTL_S:
            return it["meta"].get("label") or ""
        break
    return ""


def command(text):
    """-> (name, arg) for memory voice commands, else None."""
    if ABOUT_RE.search(text): return ("about", None)
    if FORGET_RE.match(text): return ("forget", None)
    m = NOTE_RE.match(text)
    if m and m.group(1).strip(): return ("note", m.group(1).strip().rstrip("."))
    m = DONE_RE.match(text)
    if m: return ("done", "force" if m.group(1) else None)
    return None


def run_command(store, worker_id, workspace_id, name, arg, label):
    """-> (answer_hud, answer_full). Never cites docs: these answers are about the worker's own memory."""
    where = label or "your"
    if name == "note":
        store.add_memory(worker_id, "note", arg, {"label": label, "done": False, "workspace_id": workspace_id})
        return [clamp_line(f"Saved to {where} notes"), clamp_line(f"Open loop: {arg}")], \
            f"Saved{(' for ' + label) if label else ''}: {arg}. I'll show it next time."
    if name == "done_cancel":
        _DONE_PENDING.pop(worker_id, None)
        g = store.get_guide(worker_id)
        return ["OK, not logged", clamp_line(f"Guide stays on step {g['idx'] + 1}/{len(g['steps'])}") if g else "Nothing changed"], \
            "Okay, I didn't log it." + (f" The guide is still on step {g['idx'] + 1}." if g else "")
    if name == "done":
        g = store.get_guide(worker_id)
        today = mmdd(time.time())
        if g and g["idx"] < len(g["steps"]) - 1 and arg != "force" and not done_pending(worker_id):
            # G46 fix: "done, log it" on step 2/6 used to log the job as done. Ask first.
            _DONE_PENDING[worker_id] = time.time()
            k, n = g["idx"] + 1, len(g["steps"])
            return [clamp_line(f"Only on step {k}/{n}"), "Log it anyway? Say it again", "Or say 'log it anyway'"], \
                (f"You're on step {k} of {n}, so the job isn't finished. Finish the steps, or say 'log it anyway' "
                 f"to log it now.")
        _DONE_PENDING.pop(worker_id, None)
        if g:
            mem = store.get_memory(g["mem_id"]) if g["mem_id"] else None
            name = (mem["meta"].get("title") if mem else None) or short_title(g["title"])
            if mem:
                store.update_memory(mem["id"], meta={**mem["meta"], "done": True, "step": len(g["steps"])})
            store.end_guide(worker_id)
            text = f"Done: {name}" + (f" on {label}" if label else "") + f" ({today})"
            store.add_memory(worker_id, "job_done", text, {"label": label, "title": name})
            return [clamp_line(f"Logged: {name}"), clamp_line(label or "Saved to your memory")], \
                f"Logged it: {name}{(' on ' + label) if label else ''}."
        text = f"Done: work{(' on ' + label) if label else ''} ({today})"
        store.add_memory(worker_id, "job_done", text, {"label": label})
        return [clamp_line(f"Logged: work {('on ' + label) if label else ''}".strip())], f"Logged: {text}."
    if name == "forget":
        items = [i for i in store.memory(worker_id) if i["kind"] != "context"]
        if not items: return ["Nothing to forget"], "I don't have anything saved about you."
        store.delete_memory(items[0]["id"])
        return ["Forgot it", clamp_line(items[0]["text"])], f"Deleted: {items[0]['text']}"
    if name == "about":
        items = store.memory(worker_id)
        c = Counter(i["kind"] for i in items)
        parts = ", ".join(f"{n} {k}" for k, n in sorted(c.items())) or "nothing yet"
        return [clamp_line(f"I keep {len(items)} notes about you"), "See or delete them in app"], \
            f"I keep {len(items)} short text notes about you: {parts}. No photos or audio. You can delete any of them."
    raise ValueError(name)


def ready_card(store, worker_id, trigger, label):
    items = store.memory(worker_id)
    lab = (label or "").strip().lower()

    def rel(it):
        if it["kind"] == "context": return False
        if trigger == "start_shift" or not lab: return True
        return (it["meta"].get("label") or "").lower() == lab or lab in it["text"].lower()

    mine = [i for i in items if rel(i)]  # newest first
    open_guides = [i for i in mine if i["kind"] == "guide" and not i["meta"].get("done")]
    notes = [i for i in mine if i["kind"] == "note" and not i["meta"].get("done")]
    unfinished = [f"{g['meta'].get('title', 'Guide')}: step {g['meta'].get('step', 1)}/{g['meta'].get('total', '?')}"
                  for g in open_guides] + [n["text"] for n in notes]

    asks = [i for i in mine if i["kind"] == "ask" and not i["meta"].get("not_in_docs")]
    last_answer = " · ".join(asks[0]["meta"].get("hud") or [asks[0]["text"]]) if asks else None

    usual = None
    if open_guides:
        g = open_guides[0]["meta"]
        usual = f"Resume {g.get('title', 'guide')} step {g.get('step', 1)}"
    elif notes:
        usual = notes[0]["text"][:1].upper() + notes[0]["text"][1:]
    else:
        titles = Counter(i["meta"].get("title") for i in mine if i["kind"] == "guide" and i["meta"].get("title"))
        qs = Counter(i["meta"].get("question") for i in asks if i["meta"].get("question"))
        if titles:
            usual = f"Start: {titles.most_common(1)[0][0]}"
        elif qs:
            usual = f"Ask: {qs.most_common(1)[0][0]}"

    done = [i for i in mine if i["kind"] == "job_done"]
    head = label or ("Start of shift" if trigger == "start_shift" else "Ready")
    lines = [clamp_line(f"{head} · {short_title(done[0]['meta'].get('title') or 'work', 2)} done "
                        f"{mmdd(done[0]['ts'])}") if done else clamp_line(head)]
    other = [u for u in unfinished if u.lower() != (usual or "").lower()]
    if other:
        lines.append(clamp_line(f"Open: {other[0]}"))
    elif last_answer:
        lines.append(clamp_line(f"Last: {asks[0]['meta'].get('hud', [''])[0] or asks[0]['text']}"))
    if usual:
        lines.append(clamp_line(f"▸ {usual}"))
    if len(lines) == 1:
        lines += ["Nothing saved here yet", "Ask anything ▸"]
    return {"trigger": trigger, "label": label, "lines": lines[:3], "last_answer": last_answer,
            "usual_next_step": usual, "unfinished": unfinished}
