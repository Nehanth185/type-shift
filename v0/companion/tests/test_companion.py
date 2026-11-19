"""Headless Chromium tests for the companion app against the MOCK server.
Usage:  python3 tests/test_companion.py            (starts mock_server.py itself)
        COMPANION_URL=http://host:port python3 tests/test_companion.py   (use a running server)
Writes tests/results.json and screenshots in tests/shots/.
"""
import json, os, pathlib, shutil, socket, subprocess, sys, time, urllib.request
from playwright.sync_api import sync_playwright
# local mock must not go through any sandbox/corporate HTTP proxy
os.environ['NO_PROXY'] = os.environ['no_proxy'] = '127.0.0.1,localhost'

HERE = pathlib.Path(__file__).resolve().parent
APP = HERE.parent
SHOTS = HERE / 'shots'; SHOTS.mkdir(exist_ok=True)
REPO = APP.parents[2]
GAUGE = REPO / 'simulator' / 'test_images' / 'pressure_gauge.jpg'
R = {'checks': [], 'console_errors': [], 'page_errors': []}

def check(name, ok, detail=''):
    R['checks'].append({'name': name, 'ok': bool(ok), 'detail': str(detail)[:300]})
    print(('PASS ' if ok else 'FAIL ') + name, ('' if ok else detail), flush=True)

def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p

def hud_lines(pg):
    return [pg.inner_text(s) for s in ('#hud1', '#hud2', '#hud3')]

def hud_ok(lines):
    return len([l for l in lines if l.strip()]) <= 3 and all(len(l.replace('…', '').split()) <= 7 for l in lines)

def mini_pdf(path):
    path.write_bytes(b"%PDF-1.4\n1 0 obj<</Type /Catalog /Pages 2 0 R>>endobj\n2 0 obj<</Type /Pages /Kids [3 0 R 4 0 R] /Count 2>>endobj\n"
                     b"3 0 obj<</Type /Page /Parent 2 0 R>>endobj\n4 0 obj<</Type /Page /Parent 2 0 R>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")

proc = None
BASE = os.environ.get('COMPANION_URL')
if not BASE:
    port = free_port()
    proc = subprocess.Popen([sys.executable, str(APP / 'mock_server.py'), '--port', str(port)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    BASE = f'http://127.0.0.1:{port}'
    for _ in range(50):
        try: urllib.request.urlopen(BASE + '/health', timeout=1); break
        except Exception: time.sleep(0.1)
    urllib.request.urlopen(urllib.request.Request(BASE + '/__mock/reset', data=b'{}', method='POST'))

try:
    with sync_playwright() as p:
        exe = shutil.which('chromium') or shutil.which('chromium-browser')
        b = p.chromium.launch(executable_path=exe, headless=True, args=['--no-sandbox', '--no-proxy-server', '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'])
        ctx = b.new_context(viewport={'width': 1360, 'height': 900}, permissions=['camera'])
        pg = ctx.new_page()
        pg.on('console', lambda m: R['console_errors'].append(m.text) if m.type == 'error' else None)
        pg.on('pageerror', lambda e: R['page_errors'].append(str(e)))
        pg.on('dialog', lambda d: d.accept())

        # 1 load
        pg.goto(BASE + '/'); pg.wait_for_function("document.querySelector('#connTxt').textContent.includes('server')", timeout=5000)
        check('01 page loads without JS errors', not R['page_errors'], R['page_errors'])
        check('02 connection indicator says MOCK server', 'MOCK' in pg.inner_text('#connTxt'), pg.inner_text('#connTxt'))

        # 2 ask (answer mode, typed)
        pg.fill('#question', 'Is this pressure normal?'); pg.click('#askBtn')
        pg.wait_for_function("document.querySelector('#hud1').textContent.includes('PSI')", timeout=5000)
        lines = hud_lines(pg)
        check('03 typed ask fills HUD with answer', 'CP-450' in lines[0], lines)
        check('04 HUD has <=3 lines, each <=7 words, none cut off by width', hud_ok(lines) and pg.evaluate('window.__app.hudOverflow') == 0, lines)
        check('05 HUD tag shows MOCK', 'MOCK' in pg.inner_text('#hudTag'), pg.inner_text('#hudTag'))
        check('06 full answer shown', '55-70 PSI' in pg.inner_text('#answerFull'))
        check('07 latency shown', 'ms' in pg.inner_text('#latency'), pg.inner_text('#latency'))
        pg.click('#sources button.src')
        det = pg.inner_text('#sourceDetail')
        check('08 clicking a source shows its page + snippet', pg.is_visible('#sourceDetail') and 'page 14' in det and 'Table 2' in det, det)
        pg.screenshot(path=str(SHOTS / '01_ask_answer.png'), full_page=True)

        # 3 photo upload is sent as image_b64
        pg.set_input_files('#photoIn', str(GAUGE)); pg.wait_for_selector('#photoPreview:not(.hidden)')
        with pg.expect_request(lambda r: r.url.endswith('/ask') and r.method == 'POST') as rq:
            pg.click('#askBtn')
        body = json.loads(rq.value.post_data)
        check('09 photo upload is sent as raw base64 JPEG in image_b64', body.get('image_b64', '').startswith('/9j/') and len(body['image_b64']) > 1000, len(body.get('image_b64', '')))
        last = json.loads(urllib.request.urlopen(BASE + '/__mock/last').read())
        check('10 server received workspace_id, worker_id, mode', last.get('workspace_id') == 'default' and last.get('worker_id') == 'w1' and last.get('mode') == 'answer', last)
        pg.screenshot(path=str(SHOTS / '02_ask_with_photo.png'), full_page=True)
        pg.click('#clearPhoto')
        check('11 remove photo clears it', pg.evaluate('window.__app.photoB64') is None)

        # 4 camera (fake device) snapshot
        pg.click('#camBtn'); pg.wait_for_function("document.querySelector('#camVideo').videoWidth > 0", timeout=5000)
        pg.click('#snapBtn')
        check('12 camera snapshot attaches a photo', (pg.evaluate('window.__app.photoB64') or '').startswith('/9j/'))
        pg.click('#clearPhoto')

        # 5 HUD clamp when server breaks the 3x7 rule
        pg.fill('#question', 'long test'); pg.click('#askBtn')
        pg.wait_for_function("!document.querySelector('#hudWarn').classList.contains('hidden')", timeout=5000)
        lines = hud_lines(pg)
        check('13 over-long server HUD is clamped to 3 lines x 7 words, with warning', hud_ok(lines) and lines[0].endswith('…'), lines)

        # 6 guide mode
        pg.check('input[name=mode][value=guide]'); pg.fill('#question', 'Show me the seal swap'); pg.click('#askBtn')
        pg.wait_for_selector('#guideBtns:not(.hidden)')
        s1 = hud_lines(pg); c1 = pg.inner_text('#guideCount')
        check('14 guide starts at step 1, prev disabled', 'Step 1/6' in s1[0] and pg.is_disabled('#prevBtn') and c1 == 'Step 1 of 6', [s1, c1])
        pg.click('#nextBtn'); pg.wait_for_function("document.querySelector('#guideCount').textContent==='Step 2 of 6'")
        s2 = hud_lines(pg)
        check('15 Next goes to step 2, source line fits (p.22 visible)', 'Step 2/6' in s2[0] and hud_ok(s2) and 'p.22' in s2[2] and pg.evaluate('window.__app.hudOverflow') == 0, s2)
        pg.screenshot(path=str(SHOTS / '03_guide_step2.png'), full_page=True)
        pg.click('#prevBtn'); pg.wait_for_function("document.querySelector('#guideCount').textContent==='Step 1 of 6'")
        check('16 Prev goes back to step 1', 'Step 1/6' in hud_lines(pg)[0])
        pg.click('body', position={'x': 5, 'y': 300})
        for _ in range(7):
            if pg.inner_text('#guideCount') == 'Step 6 of 6': break
            cur = pg.inner_text('#guideCount'); pg.keyboard.press('ArrowRight')
            pg.wait_for_function(f"document.querySelector('#guideCount').textContent!=={json.dumps(cur)}")
        at_last = pg.inner_text('#guideCount') == 'Step 6 of 6' and 'Finish' in pg.inner_text('#nextBtn') and pg.is_enabled('#nextBtn')
        pg.click('#nextBtn'); pg.wait_for_function("document.querySelector('#guideCount').textContent==='All 6 steps done'")
        check('17 ArrowRight walks to last step; Next becomes Finish; Finish -> all done, Next disabled',
              at_last and pg.is_disabled('#nextBtn') and 'All 6 steps done' in hud_lines(pg)[0], [pg.inner_text('#guideCount'), hud_lines(pg)])
        pg.check('input[name=mode][value=answer]')

        # 7 ready-card triggers
        pg.fill('#ctxLabel', 'CP-450'); pg.click('button[data-trigger=see_machine]')
        pg.wait_for_function("document.querySelector('#hudTag').textContent.includes('READY CARD')")
        rc = hud_lines(pg)
        check('18 See machine shows ready card (open loop)', 'seal done' in rc[0] and 'Leak check' in rc[1] and hud_ok(rc), rc)
        check('19 ready card details (unfinished, next step) shown', 'Unfinished' in pg.inner_text('#ctxInfo') and pg.is_hidden('#guideBtns'))
        pg.screenshot(path=str(SHOTS / '04_ready_card.png'), full_page=True)
        pg.click('button[data-trigger=open_job]'); pg.wait_for_function("document.querySelector('#hud1').textContent.includes('glasses-v0')")
        pg.click('button[data-trigger=start_shift]'); pg.wait_for_function("document.querySelector('#hud1').textContent.includes('beds')")
        check('20 Open job and Start shift triggers work', 'beds' in hud_lines(pg)[0])

        # 8 docs: list, upload, delete
        pg.click('#tab-docs'); pg.wait_for_selector('#docRows tr[data-doc-id]')
        n0 = pg.locator('#docRows tr[data-doc-id]').count()
        check('21 docs list loads seeded docs', n0 == 3, n0)
        pdf = HERE / 'Test Pump Manual.pdf'; mini_pdf(pdf)
        pg.set_input_files('#docFile', str(pdf)); pg.click('#uploadBtn')
        pg.wait_for_function(f"document.querySelectorAll('#docRows tr[data-doc-id]').length === {n0 + 1}")
        st = pg.inner_text('#uploadStatus')
        check('22 upload PDF adds a doc with title/pages/chunks', 'Test Pump Manual' in st and '2 pages' in st and 'Test Pump Manual' in pg.inner_text('#docRows'), st)
        pg.screenshot(path=str(SHOTS / '05_docs.png'), full_page=True)
        pg.locator('#docRows tr', has_text='Test Pump Manual').locator('button.del').click()
        pg.wait_for_function(f"document.querySelectorAll('#docRows tr[data-doc-id]').length === {n0}")
        check('23 delete doc removes it', 'Test Pump Manual' not in pg.inner_text('#docRows'))
        pg.click('#uploadBtn')
        check('24 upload with no file shows an error, no request', 'Pick a file' in pg.inner_text('#uploadStatus'))

        # 9 memory
        pg.click('#tab-memory'); pg.wait_for_selector('#memList li[data-mem-id]')
        items = pg.locator('#memList li[data-mem-id]').count()
        check('25 memory lists seed items + items learned from asks', items >= 4, items)
        check('26 asks were remembered as text', 'Asked: Is this pressure normal?' in pg.inner_text('#memList'))
        pg.screenshot(path=str(SHOTS / '06_memory.png'), full_page=True)
        pg.locator('#memList li', has_text='Prefers short steps').locator('button.del').click()
        pg.wait_for_function(f"document.querySelectorAll('#memList li[data-mem-id]').length === {items - 1}")
        check('27 delete one memory item', 'Prefers short steps' not in pg.inner_text('#memList'))
        pg.click('#forgetAll'); pg.wait_for_function("document.querySelectorAll('#memList li[data-mem-id]').length === 0")
        check('28 forget everything empties memory', 'Nothing remembered' in pg.inner_text('#memList') and 'Forgot' in pg.inner_text('#memStatus'))
        api_items = json.loads(urllib.request.urlopen(BASE + '/memory?worker_id=w1').read())['items']
        check('29 server memory really empty after forget-all', api_items == [], api_items)
        pg.screenshot(path=str(SHOTS / '07_memory_forgotten.png'), full_page=True)

        # 10 settings: worker id persists, bad URL handled
        pg.click('#tab-settings'); pg.fill('#workerId', 'w2'); pg.click('#saveSettings')
        pg.wait_for_function("document.querySelector('#settingsStatus').textContent==='Saved'")
        pg.reload(); pg.click('#tab-settings')
        check('30 settings persist across reload', pg.input_value('#workerId') == 'w2')
        pg.fill('#serverUrl', 'not a url'); pg.click('#saveSettings')
        check('31 invalid server URL rejected', 'must start with http' in pg.inner_text('#settingsStatus'))
        pg.fill('#serverUrl', 'http://127.0.0.1:9'); pg.click('#saveSettings')
        pg.wait_for_function("document.querySelector('#connTxt').textContent==='offline'", timeout=10000)
        check('32 unreachable server shows offline + banner', pg.is_visible('#banner') and "Can't reach" in pg.inner_text('#banner'), pg.inner_text('#banner'))
        pg.screenshot(path=str(SHOTS / '08_settings_offline.png'), full_page=True)
        pg.click('#tab-ask'); pg.fill('#question', 'pressure?'); pg.click('#askBtn')
        pg.wait_for_function("document.querySelector('#hudTag').textContent.includes('ERROR')")
        check('33 ask while offline shows error on HUD, no crash', "Can't reach" in hud_lines(pg)[0] and not R['page_errors'], hud_lines(pg))
        pg.click('#tab-settings'); pg.fill('#serverUrl', BASE); pg.click('#saveSettings')
        pg.wait_for_function("document.querySelector('#connTxt').textContent.includes('MOCK')")
        check('34 pointing back at the server reconnects', True)

        # 11 mobile layout
        m = b.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
        mp = m.new_page(); mp.on('pageerror', lambda e: R['page_errors'].append(str(e)))
        mp.goto(BASE + '/'); mp.fill('#question', 'Which pins are I2C on this board?'); mp.click('#askBtn')
        mp.wait_for_function("document.querySelector('#hud1').textContent.includes('SDA')")
        ov = mp.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        check('35 mobile: no horizontal overflow, HUD readable', ov and hud_ok(hud_lines(mp)), hud_lines(mp))
        mp.screenshot(path=str(SHOTS / '09_mobile.png'), full_page=True)
        mp.locator('.hud').screenshot(path=str(SHOTS / '10_hud_closeup.png'))

        real_errors = [e for e in R['console_errors'] if 'ERR_CONNECTION_REFUSED' not in e and 'Failed to load resource' not in e]
        check('36 no unexpected console errors / page errors', not real_errors and not R['page_errors'], [real_errors, R['page_errors']])
        b.close()
finally:
    if proc: proc.terminate()
    try: (HERE / 'Test Pump Manual.pdf').unlink()
    except FileNotFoundError: pass

passed = sum(c['ok'] for c in R['checks']); total = len(R['checks'])
R['summary'] = f'{passed}/{total} passed'; R['base_url'] = BASE
(HERE / 'results.json').write_text(json.dumps(R, indent=1))
print(R['summary'])
sys.exit(0 if passed == total else 1)
