# Build steps: unboxing to the first answer on the HUD

You need a laptop on 2.4 GHz Wi-Fi (or a phone hotspot), a USB-C **data** cable (charge-only cables won't work), a soldering iron and a breadboard. Allow about 2-3 hours.

**A. Laptop server (do this before the parts arrive)**
1. Install Python 3.10 or newer. Then, in `prototype/v0/server/`, run `pip install -r requirements.txt`.
2. Run `python server.py`. It prints a line like `SERVER_URL "http://192.168.1.50:8000"`. Write that address down.
3. Open a second terminal and run `python fake_device.py`. You should see an `OLED preview` box, and a spoken `answer.wav` gets saved. That proves the server works (mock mode, no key yet).
4. For real answers: create an NVIDIA API key at build.nvidia.com and put `NVIDIA_API_KEY=nvapi-...` in `server/.env` (copy `.env.example`). Restart the server, run step 3 again, and the answer should now describe the photo.

**B. Firmware onto the board (USB only, no wiring yet)**
5. Install the **Arduino IDE 2**. Under *File > Preferences > Additional boards URLs*, add `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`. Then *Boards Manager*: install **esp32 by Espressif** (3.x). *Library Manager*: install **Adafruit SSD1306** (say yes to its extras), **Adafruit GFX**, **ArduinoJson** and **OV5640 Auto Focus for ESP32 Camera** (newer boards ship a camera that needs it to focus).
6. Unbox the XIAO. Click the antenna onto the gold connector: hook one side in, then press the other side down. Press the camera board onto the B2B connector until it clicks.
7. Copy `firmware/glasses_v0/secrets.example.h` to `secrets.h` in the same folder. Fill in your Wi-Fi name, Wi-Fi password and the SERVER_URL from step 2.
8. Open `glasses_v0.ino`. Under *Tools*, set Board **XIAO_ESP32S3**, PSRAM **OPI PSRAM**, USB CDC On Boot **Enabled**, and pick the port. Click **Upload**. If no port shows up, hold **BOOT**, plug in USB, then let go.
9. Open the *Serial Monitor* at 115200 and press the board's **RESET** button. You should see `PSRAM yes`, `[CAM] sensor PID ...` and `[WIFI] ... IP ...`. Type `a` and press Enter. The board takes a photo, and the server terminal prints `[ask] ... HUD: ...`. **The whole loop now works, apart from the screen and sound.** Tip: open `http://<laptop-ip>:8000/last` in a browser to see the photo it took.

**C. Wire it up, one part at a time (unplug USB first every time; see WIRING.md)**
10. **OLED** (wires 1-4). Plug USB back in, type `d`, and test text should appear. If the Serial Monitor says `OLED NOT FOUND`, swap SDA and SCL. Text backwards? It's meant to be: that's mirror mode for the beam splitter. Type `m` to switch it off while you test on the bench.
11. **Amp + transducer** (wires 5-12). Type `t`, then hold the transducer against the bone in front of your ear. You should hear a 1-second beep. If not, check DIN/BCLK/LRC and the SD wire on D3.
12. **Button** (wires 13-14). Tap it: you should see *Looking... > Thinking...*, then the answer on the OLED, and hear it spoken. Hold it while you talk (up to 6 s) to ask a spoken question. Double-tap switches mirror mode.
13. **Battery** (last). Unplug USB. Solder red to BAT+ and black to BAT- (the pad nearer the USB port). Check the polarity twice. After 60 s with no taps it sleeps; one tap wakes it and asks.

**D. Onto the glasses**
14. Mount the OLED facing the beam-splitter cube, with the cube in front of one eye, and turn mirror mode on (the default). Put the magnifier lens between the OLED and the cube, then slide it back and forth until the text looks sharp. Aim the camera where you look: check by tapping, then opening `/last`.

**If something fails:** the Serial Monitor tells you which part. `No Wi-Fi` means the network isn't 2.4 GHz or the password is wrong. `Server not reachable` means the laptop firewall is blocking port 8000, or the two devices are on different networks. `Camera error` means the camera board needs reseating.
