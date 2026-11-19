#!/usr/bin/env python3
"""Grounded-answer eval: ask the server questions over the sample workspace "demo" and score each answer.

  python3 tests/grounded_eval.py --provider mock                    # starts its own server, mock LLM
  python3 tests/grounded_eval.py --provider nim --out results.jsonl # real model (NVIDIA_API_KEY), resumable
  python3 tests/grounded_eval.py --server http://127.0.0.1:8770 ... # use a server that is already running (SEED_DEMO=1)
  options: --ids 1,2,3   --report results.jsonl (score an existing file, no calls)

Cases: tests/grounded_cases.json. Every ask goes through POST /ask (JSON work API), so latency is end to end:
retrieval + relevance gate + LLM + formatting, measured by the client.

Scores per case:
  correct            answerable, a key fact is in the answer, and the FIRST source (the HUD source line) is the right doc+page
  correct_wrong_page fact right, cited page wrong
  false_refusal      answerable, but the server said "Not in your docs"
  wrong              answered, key fact missing, no unsupported number
  refused_ok         not answerable, server said "Not in your docs"
  hallucination      not answerable but the server answered with a claim, OR any answer containing a number that is
                     in neither the docs nor the question (automatic check; flagged answers are listed for a human look)
  flagged_unverified an unsupported number that the server's number guard caught and labelled "check it yourself"
  error              HTTP error or the "AI service error" fallback
Refusal layer is recorded: "gate" (retrieval found nothing, no LLM call) vs "llm" (the model said NOT_IN_DOCS).
"""
import argparse, base64, json, os, pathlib, re, socket, statistics, subprocess, sys, tempfile, time, urllib.error, urllib.request

HERE = pathlib.Path(__file__).resolve().parent
SERVER = HERE.parent
IMG = SERVER.parents[2] / 'simulator' / 'test_images'
DOCKEY = {'cp450': 'CP-450', 'iv': 'IV Pump', 'bench': 'bench'}
os.environ['NO_PROXY'] = os.environ['no_proxy'] = '127.0.0.1,localhost'


def corpus_text():
    t = ''
    for p in (SERVER / 'sample_workspace').rglob('*.md'): t += p.read_text() + '\n'
    return t


def nums(s):
    return {n.lstrip('0') or '0' for n in re.findall(r'\d+(?:\.\d+)?', s or '')}


def score(case, r, corpus_nums):
    if r.get('http') != 200 or r.get('error'):
        return 'error'
    full = r.get('answer_full', '')
    low = full.lower()
    srcs = r.get('sources') or []
    refused = bool(r.get('not_in_docs'))
    extra = nums(full) - corpus_nums - nums(case['q'])
    r['unsupported_numbers'] = sorted(extra)
    if r.get('unverified_numbers') and extra <= set(r['unverified_numbers']):
        return 'flagged_unverified'  # the server's number guard caught it: not shown as a doc fact, worker told to check
    if case.get('refuse'):
        if refused: return 'refused_ok'
        if full.startswith("Your docs don't mention"): return 'hedged_closest'  # mock: says it's missing, quotes closest
        if any(a.lower() in low for a in case.get('also_ok', [])): return 'answered_from_photo_ok'
        return 'hallucination'
    if refused: return 'false_refusal'
    hit = any(a.lower() in low for a in case['any'])
    first = srcs[0] if srcs else {}
    page_ok = DOCKEY[case['doc']].lower() in first.get('title', '').lower() and str(first.get('page')) in (case['page'] if isinstance(case['page'], list) else [case['page']])
    if extra: return 'hallucination'
    if hit and page_ok: return 'correct'
    if hit: return 'correct_wrong_page'
    return 'wrong'


def ask(base, case, timeout=120):
    body = {'workspace_id': 'demo', 'worker_id': 'eval', 'question': case['q'], 'mode': 'answer'}
    if case.get('image'):
        body['image_b64'] = base64.b64encode((IMG / case['image']).read_bytes()).decode()
    req = urllib.request.Request(base + '/ask', json.dumps(body).encode(), {'Content-Type': 'application/json'}, method='POST')
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            d = json.loads(resp.read()); d['http'] = resp.status
    except urllib.error.HTTPError as e:
        d = {'http': e.code, 'detail': e.read()[:300].decode('utf-8', 'replace')}
    except Exception as e:  # noqa: BLE001
        d = {'http': 0, 'detail': str(e)[:300]}
    d['client_latency_s'] = round(time.time() - t, 2)
    return d


def start_server(provider):
    tmp = pathlib.Path(tempfile.mkdtemp(prefix='geval-'))
    s = socket.socket(); s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]; s.close()
    env = {**os.environ, 'LLM_PROVIDER': provider, 'TTS_ENGINE': 'none', 'STT_ENGINE': 'none', 'RETRIEVER': os.environ.get('RETRIEVER','bm25'),
           'GLASSES_DB': str(tmp / 'g.db'), 'LOG_DIR': str(tmp / 'logs'), 'SEED_DEMO': '1', 'AUTH_MODE': 'off', 'DEVICE_TOKEN': ''}
    p = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'server:app', '--port', str(port), '--log-level', 'warning'],
                         cwd=SERVER, env=env, stdout=open(tmp / 'server.log', 'w'), stderr=subprocess.STDOUT)
    base = f'http://127.0.0.1:{port}'
    for _ in range(150):
        try: urllib.request.urlopen(base + '/health', timeout=1); return p, base
        except Exception: time.sleep(0.1)
    raise RuntimeError((tmp / 'server.log').read_text()[-2000:])


def report(rows, cases):
    by = {c['id']: c for c in cases}
    cnt = {}
    for r in rows: cnt[r['score']] = cnt.get(r['score'], 0) + 1
    ans = [r for r in rows if not by[r['id']].get('refuse')]
    ref = [r for r in rows if by[r['id']].get('refuse')]
    lat = sorted(r['client_latency_s'] for r in rows if r['score'] != 'error')
    llm_lat = sorted(r['client_latency_s'] for r in rows if r['score'] != 'error' and r.get('refusal_layer') != 'gate')
    p = lambda xs, q: xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))] if xs else None
    out = {'n': len(rows), 'counts': cnt,
           'answerable_correct': f"{sum(r['score'] == 'correct' for r in ans)}/{len(ans)}",
           'refusals_correct': f"{sum(r['score'] in ('refused_ok', 'answered_from_photo_ok') for r in ref)}/{len(ref)}",
           'false_refusals': f"{sum(r['score'] == 'false_refusal' for r in ans)} ({sum(r['score'] == 'false_refusal' and r.get('refusal_layer') == 'gate' for r in ans)} by retrieval gate)",
           'hallucinations': sum(r['score'] == 'hallucination' for r in rows),
           'flagged_by_number_guard': sum(r['score'] == 'flagged_unverified' for r in rows),
           'refused_by_gate': sum(r.get('refusal_layer') == 'gate' for r in rows),
           'refused_by_llm': sum(r.get('refusal_layer') == 'llm' for r in rows),
           'latency_all_s': {'median': statistics.median(lat) if lat else None, 'p90': p(lat, .9), 'max': lat[-1] if lat else None},
           'latency_llm_calls_s': {'n': len(llm_lat), 'median': statistics.median(llm_lat) if llm_lat else None, 'p90': p(llm_lat, .9)}}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--provider', default='mock'); ap.add_argument('--server'); ap.add_argument('--ids')
    ap.add_argument('--out'); ap.add_argument('--report'); ap.add_argument('--sleep', type=float, default=0.3)
    a = ap.parse_args()
    cases = json.load(open(HERE / 'grounded_cases.json'))
    if a.report:
        rows = [json.loads(l) for l in open(a.report)]
        print(json.dumps(report(rows, cases), indent=1)); return
    if a.ids: cases = [c for c in cases if c['id'] in set(map(int, a.ids.split(',')))]
    out = pathlib.Path(a.out or f'/tmp/grounded_{a.provider}.jsonl')
    done = {json.loads(l)['id'] for l in open(out)} if out.exists() else set()
    proc = None
    base = a.server
    if not base: proc, base = start_server(a.provider)
    cn = nums(corpus_text())
    try:
        for c in cases:
            if c['id'] in done: continue
            r = ask(base, c)
            # gate refusals come back in a few ms; a model refusal costs a model call
            if r.get('not_in_docs'): r['refusal_layer'] = 'gate' if r.get('refused_by') == 'retrieval' else 'llm'
            r['id'] = c['id']; r['q'] = c['q']; r['score'] = score(c, r, cn)
            keep = {k: r.get(k) for k in ('id', 'q', 'score', 'answer_hud', 'answer_full', 'sources', 'not_in_docs', 'refusal_layer',
                                           'unsupported_numbers', 'unverified_numbers', 'latency_ms', 'client_latency_s', 'http', 'detail', 'error')}
            keep['sources'] = [f"{s['title']} p.{s['page']}" for s in (r.get('sources') or [])]
            with open(out, 'a') as f: f.write(json.dumps(keep) + '\n')
            print(f"#{c['id']:>2} {keep['score']:<20} {keep['client_latency_s']:>6}s  {' / '.join(keep['answer_hud'] or [])}", flush=True)
            time.sleep(a.sleep if a.provider != 'mock' else 0)
    finally:
        if proc: proc.terminate()
    rows = [json.loads(l) for l in open(out)]
    print(json.dumps(report(rows, json.load(open(HERE / 'grounded_cases.json'))), indent=1))


if __name__ == '__main__':
    main()
