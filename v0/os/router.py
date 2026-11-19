"""Intent router: text (a speech transcript) + optional photo + what's active -> (app, intent, routed_by, match).

Order: an app the worker tapped > OS commands > answers to a pending question (gauge confirm) > memory commands >
navigation inside what's open (guide next/back, checklist tick) > rules per app > LLM classifier (only with a real
model; never in mock) > keyword overlap with app examples > Ask. Every result says how it was routed.
"""
import re

import memory
from llm import message_text

from .apps import APPS

ROUTE_LLM_TIMEOUT = 6


def R(p): return re.compile(p, re.I)


OS_RULES = [
    (R(r"^\s*(?:go\s+)?home\s*[.!]?$"), "os.home"),
    (R(r"^\s*(?:open|switch to|launch|go to)\s+(?:the\s+)?(\w+)(?:\s+app)?\s*[.!]?$"), "os.open"),
    (R(r"\b(?:shorter|too long|less detail|keep it short|be brief)\b"), "os.shorter"),
    (R(r"^\s*(?:more detail|more details|tell me more|explain more|longer|more)\s*[.!]?$"), "os.more"),
    (R(r"\b(?:stop|pause|turn off) (?:learning|watching how i work)\b"), "os.learning_off"),
    (R(r"\b(?:start|turn on|resume) learning\b"), "os.learning_on"),
    (R(r"\bwhat (?:have you|did you) learn"), "os.model"),
    (R(r"\bforget everything\b"), "os.forget_all"),
    (R(r"^\s*(?:dismiss|not now|no thanks|hide that)\s*[.!]?$"), "os.dismiss"),
]
YES = R(r"^\s*(?:yes|yeah|yep|correct|confirm(?:ed)?|that'?s right|right)\s*[.!]?$")
NO = R(r"^\s*(?:no|nope|wrong|that'?s wrong|incorrect)\s*[.!]?$")
GUIDE_NEXT = R(r"^\s*(?:next(?: step)?|go on|continue|ok next|done next)\s*[.!]?$")
GUIDE_PREV = R(r"^\s*(?:back|previous(?: step)?|go back|last step)\s*[.!]?$")
GUIDE_GOTO = R(r"\b(?:go to |jump to |show )?step\s*(\d{1,2})\b")
CHECK_TICK = R(r"^\s*(?:check(?:ed)?(?: it)?(?: off)?|tick(?: it)?|item done|done item|next item|ok checked)\s*[.!]?$")
APP_RULES = [
    ("handoff", "handoff.summary", R(r"\b(?:hand ?-?off|end of (?:my )?shift|shift (?:summary|report)|wrap (?:it )?up)\b")),
    ("notes", "notes.list", R(r"\b(?:open loops|my notes|what'?s open|read (?:my )?notes)\b")),
    ("checklist", "checklist.start", R(r"\b(?:checklist|check list|pm (?:on|for)|\bpm\b|preventive|preventative|inspection round)\b")),
    ("guide", "guide.start", R(r"\b(?:how do i|how to|how should i|walk me through|step by step|steps (?:for|to)|procedure for|guide me)\b")),
    ("gauge", "gauge.read", R(r"\b(?:reads?|reading(?: is)?|gauge (?:says|shows|is at))\s*\d|\b\d+(?:\.\d+)?\s*(?:psi|bar|kpa|°\s?[cf]|degrees)\b")),
    ("navigator", "nav.start", R(r"\b(?:troubleshoot\w*|diagnos\w*|fault[- ]?find\w*|what'?s wrong|find the (?:fault|problem|cause)|"
                                 r"leak(?:ing|s|y)?|dripping|no flow|low flow|low (?:discharge )?pressure|not pumping|noisy|"
                                 r"noise|vibrat\w*|rattl\w*|overheat\w*|runs hot|trip(?:s|ping|ped)?)\b")),
]
NAV_STOP = R(r"^\s*(?:stop|cancel|end|quit|exit)(?: (?:troubleshooting|the navigator|navigator|diagnosis|this))?\s*[.!]?$")
NAV_FIX = R(r"^\s*(?:found it|fixed(?: it)?|it was|the (?:problem|cause|fault) was|cause was|fix was|turned out|resolved|"
            r"that was it|that fixed it)\b")
NAV_ANSWER = R(r"^\s*(?:yes|yeah|yep|yup|no|nope|nah|correct|ok|okay|skip|pass|not sure|don'?t know|can'?t check|"
               r"cannot check|not (?:it|that)|that'?s not it|wrong|locked(?: out)?|lockout done|tagged out|loto done|"
               r"running|it'?s running|pump running|ready|it is|it isn'?t|there (?:are|is|aren'?t|isn'?t)|none)\b"
               r"|\d|\b(?:reads?|reading|about|around)\b")

GAUGE_WORDS = R(r"\b(?:gauge|reading|read this|what does it (?:say|read)|pressure|temp(?:erature)?|psi)\b")
APP_ALIASES = {"ask": "ask", "guide": "guide", "steps": "guide", "gauge": "gauge", "gauges": "gauge", "notes": "notes",
               "note": "notes", "memory": "notes", "checklist": "checklist", "checklists": "checklist",
               "handoff": "handoff", "hand-off": "handoff", "home": "home"}


def route(text, has_photo=False, active=None, provider=None, explicit_app=None):
    """active: {"guide": bool, "checklist": bool, "gauge_pending": bool}. -> (app, intent, routed_by, match)"""
    t = (text or "").strip()
    active = active or {}
    if explicit_app:
        if explicit_app not in APPS: raise KeyError(explicit_app)
        if not t:
            return explicit_app, APPS[explicit_app][0].default_intent, "explicit", None
        a, i, _, m = route(t, has_photo, active, None, None)
        if a == explicit_app: return a, i, "explicit", m
        return explicit_app, APPS[explicit_app][0].default_intent if explicit_app != "ask" else "ask.question", \
            "explicit", None
    for rx, intent in OS_RULES:
        m = rx.search(t)
        if m:
            if intent == "os.open":
                target = APP_ALIASES.get(m.group(1).lower())
                if not target: continue
                if target == "home": return "os", "os.home", "rule:os", None
                return target, APPS[target][0].default_intent, "rule:open", None
            return "os", intent, "rule:os", m
    if active.get("gauge_pending"):
        if YES.match(t): return "gauge", "gauge.confirm", "rule:pending", None
        if NO.match(t): return "gauge", "gauge.reject", "rule:pending", None
    if active.get("done_pending"):   # "done, log it" mid-procedure asked "log it anyway?"
        if YES.match(t): return "notes", "notes.done", "rule:pending", ("done", "force")
        if NO.match(t): return "notes", "notes.done_cancel", "rule:pending", None
    if active.get("nav"):
        hit = nav_route(t)
        if hit: return "navigator", hit, "rule:navigator", None
    if t:
        cmd = memory.command(t)
        if cmd:
            if cmd[0] == "done" and active.get("checklist") and not active.get("guide"):
                return "checklist", "checklist.tick", "rule:checklist", None
            return "notes", f"notes.{cmd[0]}", "rule:memory", cmd
    if active.get("guide"):
        if GUIDE_NEXT.match(t): return "guide", "guide.next", "rule:guide", None
        if GUIDE_PREV.match(t): return "guide", "guide.prev", "rule:guide", None
    m = GUIDE_GOTO.search(t)
    if m: return "guide", "guide.goto", "rule:guide", m
    if active.get("checklist") and CHECK_TICK.match(t):
        return "checklist", "checklist.tick", "rule:checklist", None
    for app, intent, rx in APP_RULES:
        m = rx.search(t)
        if m: return app, intent, f"rule:{app}", m
    if has_photo and (not t or GAUGE_WORDS.search(t)):
        return ("gauge", "gauge.photo", "rule:photo", None) if t else ("ask", "ask.photo", "rule:photo", None)
    if not t:
        return "os", "os.home", "rule:empty", None
    app = llm_route(provider, t)
    if app: return app, APPS[app][0].default_intent if app != "ask" else "ask.question", "llm", None
    app = keyword_route(t)
    if app: return app, APPS[app][0].default_intent, "keywords", None
    return "ask", "ask.question", "default", None


def nav_route(t):
    """While a Navigator session is open, its answers go to it (before memory and app rules). Anything else (a note,
    a question) falls through, so the worker can still ask or take a note mid-diagnosis."""
    if NAV_STOP.match(t): return "nav.stop"
    if NAV_FIX.match(t): return "nav.fix"
    cmd = memory.command(t) if t else None
    if cmd and cmd[0] == "done": return "nav.done"
    if cmd: return None
    if NAV_ANSWER.search(t) and len(t.split()) <= 8: return "nav.answer"
    return None


def keyword_route(t):
    """Mock-safe fallback: an app wins if one of its example phrases appears as whole words (Ask is the default, so
    it never needs to win here). Ties go to Ask."""
    low = " " + re.sub(r"[^a-z0-9 ]+", " ", t.lower()) + " "
    scores = {}
    for aid, (m, _) in APPS.items():
        if aid == "ask": continue
        scores[aid] = sum(1 for ex in m.examples if f" {ex} " in low)
    best = max(scores, key=scores.get) if scores else None
    if not best or scores[best] == 0 or list(scores.values()).count(scores[best]) > 1:
        return None
    return best


def llm_prompt(t):
    lines = [f"- {aid}: {m.description}" for aid, (m, _) in APPS.items()]
    return ("A worker wearing smart glasses said the sentence below. Pick the ONE app that should handle it.\n"
            + "\n".join(lines) + f"\n\nSentence: {t}\nReply with only the app id.")


def llm_route(provider, t):
    """Text-only classification with the configured real model. Mock provider, no provider, any error or an answer
    that isn't an app id -> None (the keyword/Ask fallback takes over). Never called in mock mode."""
    if provider is None or getattr(provider, "name", "mock") == "mock" or not hasattr(provider, "_post"):
        return None
    body = {"model": getattr(provider, "model", ""), "messages": [{"role": "user", "content": llm_prompt(t)}],
            "max_tokens": 8, "temperature": 0, "stream": False}
    try:
        raw = message_text(provider._post(body)).strip().lower()
    except Exception:  # noqa: BLE001  - routing must never fail because the model did
        return None
    for aid in APPS:
        if re.search(rf"\b{aid}\b", raw): return aid
    return None
