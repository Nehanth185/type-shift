"""Browser check of the Navigator in simulator/os-sim.html against a real server in mock mode (needs Playwright).
  python sim_nav_check.py  -> plays '▶ Navigator demo (2 techs)', checks tech B sees the team fix first, screenshot."""
import os, subprocess, sys, tempfile, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(os.path.dirname(HERE), "server")
env = {**os.environ, "LLM_PROVIDER": "mock", "TTS_ENGINE": "none", "STT_ENGINE": "none", "SEED_DEMO": "1", "PORT": "8792",
       "GLASSES_DB": os.path.join(tempfile.mkdtemp(), "sim.db"), "REQUEST_LOG": "0", "AUTH_MODE": "off", "DEVICE_TOKEN": ""}
env.pop("NVIDIA_API_KEY", None)
p = subprocess.Popen([sys.executable, "server.py"], cwd=SERVER, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(60):
        try: urllib.request.urlopen("http://127.0.0.1:8792/health"); break
        except Exception: time.sleep(0.5)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(**({"executable_path": "/usr/bin/chromium", "args": ["--no-sandbox"]} if os.path.exists("/usr/bin/chromium") else {}))
        pg = b.new_page(viewport={"width": 1500, "height": 1000})
        errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto("http://127.0.0.1:8792/os/sim"); pg.wait_for_selector("text=server ok")
        pg.click("#navdemo"); pg.wait_for_selector("text=Tech B sees the team fix", timeout=60000); time.sleep(0.4)
        hud, fixes, route = pg.inner_text("#hud"), pg.inner_text("#fixes"), pg.inner_text("#route")
        pg.screenshot(path=os.path.join(HERE, "sim_os_navigator.png"), full_page=True)
        b.close()
    print("HUD:\n", hud, "\nROUTE:", route, "\nFIXES:\n", fixes, "\nJS errors:", errs)
    ok = "Last time here" in hud and "p.3" in hud and "strainer" in fixes.lower() and "navigator" in route and not errs
    print("SIM NAV CHECK", "PASS" if ok else "FAIL"); sys.exit(0 if ok else 1)
finally:
    p.terminate()
