"""Browser check of simulator/os-sim.html against a real server in mock mode (not part of pytest: needs Playwright).
  python sim_check.py   -> starts server.py on :8791 with a temp DB + demo docs, plays the 3 demo shifts, screenshots."""
import os, subprocess, sys, tempfile, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(os.path.dirname(HERE), "server")
env = {**os.environ, "LLM_PROVIDER": "mock", "TTS_ENGINE": "none", "STT_ENGINE": "none", "SEED_DEMO": "1", "PORT": "8791",
       "GLASSES_DB": os.path.join(tempfile.mkdtemp(), "sim.db"), "REQUEST_LOG": "0", "AUTH_MODE": "off", "DEVICE_TOKEN": ""}
env.pop("NVIDIA_API_KEY", None)
p = subprocess.Popen([sys.executable, "server.py"], cwd=SERVER, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(60):
        try: urllib.request.urlopen("http://127.0.0.1:8791/health"); break
        except Exception: time.sleep(0.5)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(**({"executable_path": "/usr/bin/chromium", "args": ["--no-sandbox"]} if os.path.exists("/usr/bin/chromium") else {})); pg = b.new_page(viewport={"width": 1500, "height": 1000})
        errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto("http://127.0.0.1:8791/os/sim"); pg.wait_for_selector("text=server ok")
        pg.click("#demo"); pg.wait_for_selector("text=Shift 4 starts", timeout=30000); time.sleep(0.5)
        ready = pg.inner_text("#ready"); hud = pg.inner_text("#hud")
        pg.screenshot(path=os.path.join(HERE, "sim_os_home.png"), full_page=True)
        pg.click("#ready .ready button"); time.sleep(0.6)      # open the top ready card
        hud2 = pg.inner_text("#hud"); route = pg.inner_text("#route")
        pg.fill("#say", "reads 48 psi"); pg.click("#send"); time.sleep(0.6)
        hud3 = pg.inner_text("#hud")
        pg.screenshot(path=os.path.join(HERE, "sim_os_gauge.png"), full_page=True)
        b.close()
    print("READY:\n", ready, "\nHUD:\n", hud, "\nAFTER OPEN:\n", hud2, "\n", route, "\nGAUGE:\n", hud3, "\nJS errors:", errs)
    ok = "Why:" in ready and "Gauge P-12" in ready and "LOW" in hud3 and not errs
    print("SIM CHECK", "PASS" if ok else "FAIL"); sys.exit(0 if ok else 1)
finally:
    p.terminate()
