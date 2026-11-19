"""Contract test: the companion app's flows in headless Chromium against the REAL server (prototype/v0/server,
mock LLM provider, fresh SQLite DB, no network, no API key). The app is loaded from the server itself (/app/).

Usage:  python3 tests/test_companion_real.py                 # both auth modes: worker, then off
        python3 tests/test_companion_real.py --auth worker   # one mode
        python3 tests/test_companion_real.py --llm nim       # real model on NVIDIA NIM (needs NVIDIA_API_KEY; ~10 calls
                                                             # per mode). Writes results_real_nim.json, shots real_nim_*.png
Writes tests/results_real.json and screenshots tests/shots/real_*.png.

Unlike test_companion.py (canned mock answers), every check here compares what the browser shows with what the
real server actually returned or stored, read back over HTTP.
"""
import argparse, json, os, pathlib, shutil, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request
from playwright.sync_api import sync_playwright
os.environ['NO_PROXY'] = os.environ['no_proxy'] = '127.0.0.1,localhost'

HERE = pathlib.Path(__file__).resolve().parent
APP = HERE.parent
SERVER = APP.parent / 'server'
SHOTS = HERE / 'shots'; SHOTS.mkdir(exist_ok=True)
SAMPLE = SERVER / 'sample_workspace'
PDF = SAMPLE / 'cp450_pump_manual_excerpt.pdf'
MD = SAMPLE / 'unit_iv_pump_guide.md'
GAUGE = APP.parents[2] / 'simulator' / 'test_images' / 'pressure_gauge.jpg'
WS = 'plant7'
R = {'runs': {}}
LLM = 'mock'


def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p


def hud_lines(pg):
    return [pg.inner_text(s) for s in ('#hud1', '#hud2', '#hud3')]


def hud_ok(lines):
    return len([l for l in lines if l.strip()]) <= 3 and all(len(l.replace('…', '').split()) <= 7 for l in lines)


def shown(hud):  # what the HUD preview should show for a server answer_hud (server already obeys 3 x 7)
    return (list(hud) + ['', '', ''])[:3]


def run(auth, chromium_path):
    checks, console, page_errors = [], [], []
    tag = f'[{auth}] '

    def check(name, ok, detail=''):
        checks.append({'name': name, 'ok': bool(ok), 'detail': '' if ok else str(detail)[:400]})
        print(('PASS ' if ok else 'FAIL ') + tag + name, ('' if ok else str(detail)[:400]), flush=True)

    tmp = pathlib.Path(tempfile.mkdtemp(prefix='companion-real-'))
    env = {**os.environ, 'LLM_PROVIDER': LLM, 'TTS_ENGINE': 'none', 'STT_ENGINE': 'none', 'RETRIEVER': 'bm25',
           'GLASSES_DB': str(tmp / 'g.db'), 'LOG_DIR': str(tmp / 'logs'), 'AUTH_MODE': auth, 'SEED_DEMO': '',
           'DEVICE_TOKEN': ''}
    if LLM == 'mock': env.pop('NVIDIA_API_KEY', None); env.pop('LLM_API_KEY', None)
    tok = {}
    if auth == 'worker':
        for w in ('w1', 'w2'):
            tok[w] = subprocess.run([sys.executable, 'workers.py', 'add', w], cwd=SERVER, env=env, capture_output=True,
                                    text=True, check=True).stdout.strip()
    port = free_port(); base = f'http://127.0.0.1:{port}'
    proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'server:app', '--port', str(port), '--log-level', 'warning'],
                            cwd=SERVER, env=env, stdout=open(tmp / 'server.log', 'w'), stderr=subprocess.STDOUT)

    def http(method, path, body=None, who='w1'):
        h = {'Content-Type': 'application/json'} if body is not None else {}
        if auth == 'worker' and who: h['Authorization'] = 'Bearer ' + tok[who]
        req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r: return r.status, json.loads(r.read() or b'null')
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b'null')

    me = 'w1'
    mem = lambda who='w1': http('GET', f'/memory?worker_id={who}' if auth == 'off' else '/memory', who=who)[1]['items']
    try:
        for _ in range(100):
            try: urllib.request.urlopen(base + '/health', timeout=1); break
            except Exception: time.sleep(0.1)
        else:
            raise RuntimeError('server did not start: ' + (tmp / 'server.log').read_text()[-2000:])

        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=chromium_path, headless=True,
                                  args=['--no-sandbox', '--no-proxy-server'])
            ctx = b.new_context(viewport={'width': 1360, 'height': 900})
            if LLM != 'mock': ctx.set_default_timeout(45000)  # server's LLM_TIMEOUT is 18 s; leave room for it to answer
            pg = ctx.new_page()
            pg.on('console', lambda m: console.append(m.text) if m.type == 'error' else None)
            pg.on('pageerror', lambda e: page_errors.append(str(e)))
            pg.on('dialog', lambda d: d.accept())
            conn = lambda: pg.inner_text('#connTxt')

            # ---- 1 load from the server, sign in
            pg.goto(base + '/')
            pg.wait_for_function("!['not checked'].includes(document.querySelector('#connTxt').textContent)", timeout=8000)
            check('01 app is served by the real server at /app/ (root redirects), no JS errors',
                  '/app/' in pg.url and not page_errors, [pg.url, page_errors])
            if auth == 'worker':
                check('02 no token: offline + 401 banner', conn() == 'offline' and '401' in pg.inner_text('#banner'),
                      [conn(), pg.inner_text('#banner')])
            pg.click('#tab-settings')
            pg.fill('#workspaceId', WS); pg.fill('#workerId', 'somebody-else' if auth == 'worker' else 'w1')
            pg.fill('#workerToken', tok.get('w1', '')); pg.click('#saveSettings')
            pg.wait_for_function("document.querySelector('#connTxt').textContent.startsWith('connected')", timeout=8000)
            if auth == 'worker':
                check('03 token signs in: worker id comes from the token, status shows it',
                      'as w1' in conn() and pg.input_value('#workerId') == 'w1' and f'{LLM} LLM' in conn(), [conn(), pg.input_value('#workerId')])
            else:
                check(f'03 connects, not labelled MOCK server (real server, {LLM} LLM)', 'MOCK' not in conn() and f'{LLM} LLM' in conn(), conn())
            check('04 HUD not tagged MOCK on the real server', 'MOCK' not in pg.inner_text('#hudTag'))

            # ---- 2 docs
            pg.click('#tab-docs'); pg.wait_for_function("!document.querySelector('#docRows').textContent.includes('Loading')")
            check('05 empty workspace shows "No docs yet"', 'No docs yet' in pg.inner_text('#docRows'), pg.inner_text('#docRows'))
            with pg.expect_response(lambda r: r.url.endswith('/docs') and r.request.method == 'POST') as rs:
                pg.set_input_files('#docFile', str(PDF)); pg.click('#uploadBtn')
            up = rs.value.json()
            pg.wait_for_function("document.querySelectorAll('#docRows tr[data-doc-id]').length === 1")
            st = pg.inner_text('#uploadStatus')
            check('06 upload real PDF: status shows the server title/pages/chunks',
                  rs.value.status == 200 and f'"{up["title"]}"' in st and f'{up["pages"]} pages' in st and f'{up["chunks"]} chunks' in st,
                  [up, st])
            pdf_id = up['doc_id']
            pg.set_input_files('#docFile', str(MD)); pg.click('#uploadBtn')
            pg.wait_for_function("document.querySelectorAll('#docRows tr[data-doc-id]').length === 2")
            listed = http('GET', f'/docs?workspace_id={WS}')[1]
            rows = pg.eval_on_selector_all('#docRows tr[data-doc-id]', 'els => els.map(e => [e.dataset.docId, ...[...e.cells].slice(0,3).map(c => c.textContent)])')
            check('07 docs table == GET /docs (ids, titles, pages, chunks, oldest first)',
                  rows == [[d['doc_id'], d['title'], str(d['pages']), str(d['chunks'])] for d in listed], [rows, listed])
            blank = tmp / 'Scanned Manual.pdf'
            blank.write_bytes(b"%PDF-1.4\n1 0 obj<</Type /Catalog /Pages 2 0 R>>endobj\n2 0 obj<</Type /Pages /Kids [3 0 R] /Count 1>>endobj\n"
                              b"3 0 obj<</Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")
            pg.set_input_files('#docFile', str(blank)); pg.click('#uploadBtn')
            pg.wait_for_function("document.querySelector('#uploadStatus').classList.contains('err')")
            check('08 text-less PDF -> 422 shown, nothing added', '422' in pg.inner_text('#uploadStatus')
                  and pg.locator('#docRows tr[data-doc-id]').count() == 2, pg.inner_text('#uploadStatus'))
            docx = tmp / 'notes.docx'; docx.write_bytes(b'PK\x03\x04 not really a docx')
            pg.set_input_files('#docFile', str(docx)); pg.click('#uploadBtn')
            pg.wait_for_function("document.querySelector('#uploadStatus').textContent.includes('415')")
            check('09 unsupported type -> 415 shown; picker no longer offers .docx',
                  '.docx' not in pg.get_attribute('#docFile', 'accept'), pg.get_attribute('#docFile', 'accept'))
            pg.screenshot(path=str(SHOTS / f'real_{"nim_" if LLM != "mock" else ""}{auth}_01_docs.png'), full_page=True)

            # ---- 3 ask with citations
            def ask(q, mode='answer'):
                pg.check(f'input[name=mode][value={mode}]'); pg.fill('#question', q)
                with pg.expect_response(lambda r: r.url.endswith('/ask') and r.request.method == 'POST') as rs:
                    pg.click('#askBtn')
                pg.wait_for_function("!document.querySelector('#hudTag').textContent.includes('THINKING')")
                return rs.value, rs.value.request
            pg.click('#tab-ask')
            res, req = ask('Is this pressure normal?')
            a = res.json(); body = json.loads(req.post_data)
            check('10 /ask request: workspace, worker, question, mode, no image',
                  body == {'workspace_id': WS, 'worker_id': 'w1', 'question': 'Is this pressure normal?', 'mode': 'answer'}, body)
            check('11 HUD preview == server answer_hud, fits 3 x 7, no width overflow, no clamp warning',
                  hud_lines(pg) == shown(a['answer_hud']) and hud_ok(hud_lines(pg)) and pg.evaluate('window.__app.hudOverflow') == 0
                  and pg.is_hidden('#hudWarn'), [a['answer_hud'], hud_lines(pg), pg.inner_text('#hudWarn')])
            check('12 answer cites the PDF: HUD source line p.14, full answer shown',
                  a['answer_hud'][-1].startswith('Source:') and 'p.14' in a['answer_hud'][-1] and '55-70 PSI' in a['answer_full']
                  and pg.inner_text('#answerFull') == a['answer_full'], a)
            srcs = a['sources']
            ui_src = pg.eval_on_selector_all('#sources button.src', 'els => els.map(e => e.textContent)')
            check('13 sources list == server sources', ui_src == [f"{s['title']} · p.{s['page']}" for s in srcs]
                  and srcs[0]['doc_id'] == pdf_id, [ui_src, srcs])
            pg.click('#sources button.src >> nth=0'); det = pg.inner_text('#sourceDetail')
            check('14 clicking a source shows page, snippet and doc_id from the server',
                  f"page {srcs[0]['page']}" in det and srcs[0]['snippet'] in det and pdf_id in det, det)
            check('15 latency shown from server', f"{a['latency_ms']} ms" in pg.inner_text('#latency'), pg.inner_text('#latency'))
            pg.screenshot(path=str(SHOTS / f'real_{"nim_" if LLM != "mock" else ""}{auth}_02_ask.png'), full_page=True)

            pg.set_input_files('#photoIn', str(GAUGE)); pg.wait_for_selector('#photoPreview:not(.hidden)')
            res, req = ask('What is the normal discharge pressure?')
            body = json.loads(req.post_data)
            check('16 question + photo: image_b64 sent as raw JPEG base64, server answers with a citation',
                  res.status == 200 and body.get('image_b64', '').startswith('/9j/') and res.json()['sources'], [res.status, res.json()])
            res, req = ask('')
            d = res.json()
            check('17 photo only (no question): 200, marked not from docs, app shows "No source"',
                  res.status == 200 and d.get('ungrounded') is True and hud_lines(pg) == shown(d['answer_hud'])
                  and 'No source' in pg.inner_text('#sources'), [res.status, d])
            pg.click('#clearPhoto')
            res, _ = ask('What is the warranty period for the flux capacitor?')
            d = res.json()
            check('18 not in docs: HUD says so, no sources', d.get('not_in_docs') is True and hud_lines(pg)[0] == 'Not in your docs'
                  and 'No source' in pg.inner_text('#sources'), d)
            pg.fill('#question', ''); pg.click('#askBtn')
            check('19 empty question without photo blocked in the app', 'Type a question' in pg.inner_text('#askStatus'))

            # ---- 4 guide mode
            res, _ = ask('How do I replace the mechanical seal?', mode='guide')
            g = res.json()
            pg.wait_for_selector('#guideBtns:not(.hidden)')
            total = g['guide']['total'] if g.get('guide') else 0
            check('20 guide starts: step 1 of N from the doc, safety warning first, prev disabled',
                  total >= 2 and pg.inner_text('#guideCount') == f'Step 1 of {total}' and hud_lines(pg) == shown(g['answer_hud'])
                  and hud_lines(pg)[0].startswith('⚠') and pg.is_disabled('#prevBtn'), [g, hud_lines(pg)])
            overflow = [pg.evaluate('window.__app.hudOverflow')]
            pg.click('#nextBtn'); pg.wait_for_function("document.querySelector('#guideCount').textContent.startsWith('Step 2 ')")
            s2 = hud_lines(pg); overflow.append(pg.evaluate('window.__app.hudOverflow'))
            check('21 Next -> step 2 with its own source line', 'Step 2/' in ' '.join(s2) and s2[-1].startswith('Source:') and hud_ok(s2), s2)
            pg.screenshot(path=str(SHOTS / f'real_{"nim_" if LLM != "mock" else ""}{auth}_03_guide.png'), full_page=True)
            pg.click('#prevBtn'); pg.wait_for_function("document.querySelector('#guideCount').textContent.startsWith('Step 1 ')")
            check('22 Prev -> step 1', 'Step 1/' in ' '.join(hud_lines(pg)))
            pg.click('body', position={'x': 5, 'y': 300})
            for _ in range(total + 1):
                if pg.inner_text('#guideCount') == f'Step {total} of {total}': break
                cur = pg.inner_text('#guideCount'); pg.keyboard.press('ArrowRight')
                pg.wait_for_function(f"document.querySelector('#guideCount').textContent!=={json.dumps(cur)}")
                overflow.append(pg.evaluate('window.__app.hudOverflow'))
            check('23 arrow keys walk to the last step; Next becomes Finish', pg.inner_text('#guideCount') == f'Step {total} of {total}'
                  and 'Finish' in pg.inner_text('#nextBtn') and pg.is_enabled('#nextBtn'), pg.inner_text('#guideCount'))
            with pg.expect_response(lambda r: r.url.endswith('/guide/next')) as rs:
                pg.click('#nextBtn')
            pg.wait_for_function("document.querySelector('#guideCount').textContent.startsWith('All ')")
            check('24 Finish -> server finished:true, HUD "All N steps done", Next disabled',
                  rs.value.json().get('finished') is True and hud_lines(pg)[0] == f'All {total} steps done' and pg.is_disabled('#nextBtn'),
                  [rs.value.json(), hud_lines(pg)])
            check('25 no guide step overflows the HUD width', not any(overflow), overflow)
            pg.check('input[name=mode][value=answer]')
            res, _ = ask('Done, log it')
            check('26 "Done, log it" logs the guide (memory command, no sources)', res.json().get('kind') == 'memory'
                  and hud_lines(pg)[0].startswith('Logged:'), res.json())
            check('27 server guide is closed after logging', http('POST', '/guide/next', {'worker_id': 'w1'})[0] == 404)

            # ---- 5 ready cards
            pg.fill('#ctxLabel', 'CP-450')
            with pg.expect_response(lambda r: r.url.endswith('/context')) as rs:
                pg.click('button[data-trigger=see_machine]')
            pg.wait_for_function("document.querySelector('#hudTag').textContent.includes('READY CARD')")
            c = rs.value.json(); info = pg.inner_text('#ctxInfo')
            check('28 see_machine ready card: HUD == server lines, details shown', hud_lines(pg) == shown(c['lines']) and hud_ok(hud_lines(pg))
                  and (c['last_answer'] is None or c['last_answer'] in info), [c, hud_lines(pg), info])
            pg.screenshot(path=str(SHOTS / f'real_{"nim_" if LLM != "mock" else ""}{auth}_04_ready_card.png'), full_page=True)
            pg.fill('#ctxLabel', 'XJ-9 compressor')
            with pg.expect_response(lambda r: r.url.endswith('/context')) as rs:
                pg.click('button[data-trigger=open_job]')
            pg.wait_for_function("document.querySelector('#hud1').textContent.startsWith('XJ-9')")
            check('29 first visit to a new label: "Nothing saved here yet"', hud_lines(pg)[1] == 'Nothing saved here yet'
                  and 'Nothing remembered' in pg.inner_text('#ctxInfo'), [rs.value.json(), hud_lines(pg)])
            with pg.expect_response(lambda r: r.url.endswith('/context')) as rs:
                pg.click('button[data-trigger=start_shift]')
            pg.wait_for_function("document.querySelector('#hudStep').textContent === 'start shift'")
            check('30 start_shift card == server lines', hud_lines(pg) == shown(rs.value.json()['lines']), [rs.value.json(), hud_lines(pg)])

            # ---- 6 memory
            pg.click('#tab-memory'); pg.wait_for_selector('#memList li[data-mem-id]')
            server_items = mem()
            ui_ids = pg.eval_on_selector_all('#memList li[data-mem-id]', 'els => els.map(e => e.dataset.memId)')
            check('31 memory list == GET /memory (same ids, newest first)', ui_ids == [i['id'] for i in server_items] and len(ui_ids) >= 5,
                  [ui_ids, [i['id'] for i in server_items]])
            check('32 asks were remembered as text', 'Q: Is this pressure normal?' in pg.inner_text('#memList'))
            if auth == 'worker':
                check('33 another worker (w2) sees none of w1\'s memory', mem('w2') == [] and http('GET', '/memory?worker_id=w1', who='w2')[0] == 403)
            else:
                check('33 no auth: another worker id has its own (empty) memory', mem('w2') == [])
            pg.screenshot(path=str(SHOTS / f'real_{"nim_" if LLM != "mock" else ""}{auth}_05_memory.png'), full_page=True)
            victim = server_items[0]
            pg.locator(f'#memList li[data-mem-id="{victim["id"]}"] button.del').click()
            pg.wait_for_function(f"document.querySelectorAll('#memList li[data-mem-id]').length === {len(server_items) - 1}")
            after = mem()
            check('34 delete one item in the app deletes it on the server', victim['id'] not in [i['id'] for i in after]
                  and len(after) == len(server_items) - 1, after)
            pg.click('#forgetAll'); pg.wait_for_function("document.querySelectorAll('#memList li[data-mem-id]').length === 0")
            check('35 forget everything empties server memory and the count matches', mem() == []
                  and f'Forgot {len(after)} item' in pg.inner_text('#memStatus'), pg.inner_text('#memStatus'))

            # ---- 7 delete a doc, then its answers are gone
            pg.click('#tab-docs'); pg.wait_for_selector('#docRows tr[data-doc-id]')
            pg.locator(f'#docRows tr[data-doc-id="{pdf_id}"] button.del').click()
            pg.wait_for_function("document.querySelectorAll('#docRows tr[data-doc-id]').length === 1")
            check('36 delete doc in the app deletes it on the server', pdf_id not in [d['doc_id'] for d in http('GET', f'/docs?workspace_id={WS}')[1]])
            pg.click('#tab-ask'); res, _ = ask('Is this pressure normal?')
            check('37 after deleting the manual, the pressure question is "Not in your docs"', res.json().get('not_in_docs') is True, res.json())

            # ---- 8 auth failures in the app
            if auth == 'worker':
                pg.evaluate("localStorage.setItem('wg.worker', 'w2')")  # stale worker id with w1's token
                res, _ = ask('Is this pressure normal?')
                check('38 token for w1 + worker id w2 -> 403 shown, HUD error, no crash', res.status == 403
                      and "belongs to worker 'w1'" in pg.inner_text('#askStatus') and pg.inner_text('#hudTag').endswith('ERROR'),
                      [res.status, pg.inner_text('#askStatus')])
                pg.click('#tab-settings'); pg.fill('#workerToken', tok['w2']); pg.click('#saveSettings')
                pg.wait_for_function("document.querySelector('#connTxt').textContent.includes('as w2')")
                pg.click('#tab-memory'); pg.wait_for_function("document.querySelector('#memList').textContent.includes('Nothing remembered')")
                check('39 switching to w2\'s token switches identity and memory view', pg.evaluate("localStorage.getItem('wg.worker')") == 'w2')
                pg.click('#tab-settings'); pg.fill('#workerToken', 'wt_revoked_or_typo'); pg.click('#saveSettings')
                pg.wait_for_function("document.querySelector('#connTxt').textContent === 'offline'")
                check('40 wrong token -> offline + 401 banner', '401' in pg.inner_text('#banner'), pg.inner_text('#banner'))
                pg.screenshot(path=str(SHOTS / f'real_{"nim_" if LLM != "mock" else ""}{auth}_06_bad_token.png'), full_page=True)
                pg.fill('#workerToken', tok['w1']); pg.click('#saveSettings')
                pg.wait_for_function("document.querySelector('#connTxt').textContent.includes('as w1')")

            # ---- 9 mobile layout against real answers
            m = b.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
            if LLM != 'mock': m.set_default_timeout(45000)
            mp = m.new_page(); mp.on('pageerror', lambda e: page_errors.append(str(e)))
            mp.goto(base + '/app/')
            mp.evaluate(f"localStorage.setItem('wg.workspace', {json.dumps(WS)}); localStorage.setItem('wg.token', {json.dumps(tok.get('w1', ''))})")
            mp.reload(); mp.wait_for_function("document.querySelector('#connTxt').textContent.startsWith('connected')")
            mp.fill('#question', 'How do I prime the IV pump?'); mp.click('#askBtn')
            mp.wait_for_function("!['THINKING', 'READY'].some(t => document.querySelector('#hudTag').textContent.includes(t))")
            ov = mp.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
            check('41 mobile 390 px: real answer fits, no horizontal overflow', ov and hud_ok(hud_lines(mp))
                  and mp.evaluate('window.__app.hudOverflow') == 0, hud_lines(mp))
            mp.screenshot(path=str(SHOTS / f'real_{"nim_" if LLM != "mock" else ""}{auth}_07_mobile.png'), full_page=True)

            expected = ('Failed to load resource',)  # the browser logs every 4xx we provoke on purpose
            real_errors = [e for e in console if not any(x in e for x in expected)]
            check('42 no unexpected console errors / page errors', not real_errors and not page_errors, [real_errors, page_errors])
            b.close()
    except Exception as e:  # noqa: BLE001  - record the crash as a failed check, keep the other mode running
        check('CRASH ' + type(e).__name__, False, f'{e}\n--- server log ---\n' + (tmp / 'server.log').read_text()[-1500:])
    finally:
        proc.terminate(); proc.wait(5)
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(c['ok'] for c in checks)
    R['runs'][auth] = {'summary': f'{passed}/{len(checks)} passed', 'checks': checks}
    return passed, len(checks)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--auth', choices=['worker', 'off', 'both'], default='both')
    ap.add_argument('--llm', choices=['mock', 'nim'], default='mock')
    a = ap.parse_args(); LLM = a.llm
    if LLM != 'mock': R['llm'] = os.environ.get('LLM_MODEL', 'nim default (nvidia/nemotron-3.5-super-vl-120b-a12b)')
    exe = shutil.which('chromium') or shutil.which('chromium-browser')
    totals = [run(m, exe) for m in (['worker', 'off'] if a.auth == 'both' else [a.auth])]
    p_, t_ = sum(x for x, _ in totals), sum(y for _, y in totals)
    R['summary'] = f'{p_}/{t_} passed'
    R['ran_at'] = time.strftime('%Y-%m-%d %H:%M:%S %Z')
    (HERE / ('results_real.json' if LLM == 'mock' else f'results_real_{LLM}.json')).write_text(json.dumps(R, indent=1))
    print(R['summary'])
    sys.exit(0 if p_ == t_ else 1)
