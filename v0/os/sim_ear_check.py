"""Browser check of simulator/machine-ear-sim.html against a real server in mock mode (needs Playwright + Chromium).
  python sim_ear_check.py  -> plays '▶ Walk-by demo', checks the ALERT fired and the tap opened the Navigator, screenshots."""
import os, subprocess, sys, tempfile, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(os.path.dirname(HERE), "server")
env = {**os.environ, "LLM_PROVIDER": "mock", "TTS_ENGINE": "none", "STT_ENGINE": "none", "SEED_DEMO": "1", "PORT": "8793",
       "GLASSES_DB": os.path.join(tempfile.mkdtemp(), "sim.db"), "REQUEST_LOG": "0", "AUTH_MODE": "off", "DEVICE_TOKEN": ""}
env.pop("NVIDIA_API_KEY", None)
p = subprocess.Popen([sys.executable, "server.py"], cwd=SERVER, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(60):
        try: urllib.request.urlopen("http://127.0.0.1:8793/health"); break
        except Exception: time.sleep(0.5)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(**({"executable_path": "/usr/bin/chromium", "args": ["--no-sandbox"]} if os.path.exists("/usr/bin/chromium") else {}))
        pg = b.new_page(viewport={"width": 1500, "height": 900})
        errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto("http://127.0.0.1:8793/os/ear/sim"); pg.wait_for_selector("text=server ok")
        if len(sys.argv) > 1: pg.select_option("#snr", sys.argv[1])
        pg.click("#demo"); pg.wait_for_selector("text=DEMO DONE", timeout=90000); time.sleep(0.4)
        hud, steps, log = pg.inner_text("#hud"), pg.inner_text("#steplog"), pg.inner_text("#log")
        shot = os.path.join(HERE, "sim_machine_ear.png" if len(sys.argv) == 1 else f"sim_machine_ear_snr{sys.argv[1]}.png")
        pg.screenshot(path=shot, full_page=True)
        pg.goto("http://127.0.0.1:8793/os/ear/sim?notap"); pg.wait_for_selector("text=server ok")
        pg.click("#demo"); pg.wait_for_selector("text=DEMO DONE", timeout=90000); time.sleep(0.3)
        alert_hud = pg.inner_text("#hud")
        pg.locator("#hud").screenshot(path=os.path.join(HERE, "sim_machine_ear_alert_hud.png"))
        b.close()
    print("ALERT HUD:\n", alert_hud)
    print("HUD:\n", hud, "\nSTEPS:\n", steps, "\nPASSES:\n", log, "\nJS errors:", errs)
    ok = "alert → Navigator" in steps and 'symptom "noise"' in steps and "Check 1" in hud and "ALERT" in alert_hud and not errs
    print("SIM EAR CHECK", "PASS" if ok else "FAIL", shot); sys.exit(0 if ok else 1)
finally:
    p.terminate()
