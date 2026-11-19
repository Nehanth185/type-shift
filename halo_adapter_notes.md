# Halo adapter notes (Brilliant Labs Halo → our answer server)
Checked 2026-11-12. Research only; nothing bought. Tags: [F] = read on the page today · [EX] = search excerpt · [EST] = my estimate · [P] = our project files.

## 1. Can we buy one, and will it arrive?
- **Store page: $399, one SKU (Black, BLHALOBLK), `available: true`, normal "Add to cart", no pre-order wording** [F, https://brilliant.xyz/products/halo and its Shopify product JSON]. Taxes not collected at checkout; $49 restocking fee on non-defect returns [F].
- Brilliant says it began shipping **in batches on Oct 27, 2026** after a year of missed dates (GamesBeat, Dirty Static) [EX].
- **But delivery is shaky:** a Nov 8-9 r/SmartGlasses megathread says many 2025 pre-orders still have no glasses and buyers were banned from Discord for asking; other owners report recent shipping notices [EX, reddit + media-factory.net 2026-11-10].
- **My read:** new orders probably ship, but queued behind a backlog of pre-orders. Plan for weeks of delay, not days. Pay by card (chargeback protection). Brilliant's own setup guide still lists the Noa phone app as "coming soon" [EX], which doesn't matter for us because we use the SDK.
- Fallback if it slips: the official **halo-emulator** (Python, renders the 256x256 display, injects button/tap/BLE events) lets us build and test the adapter before hardware arrives [F].

## 2. Hardware facts that shape the adapter [F, docs.brilliant.xyz]
| Thing | Halo | What it means for us |
|---|---|---|
| Display | **256 x 256 round**, color (0xRRGGBB), draws immediately (no buffer flip) | Our HUD rule (3 lines x 7 words) does NOT fit; see section 4 |
| Fonts | Dogica / DogicaBold, 8 px pixel font, sizes in multiples of 8 | 16 px ≈ 16 chars across the middle, fewer near the top/bottom of the circle [EST] |
| Camera | JPEG, **640 px only**; ~80 / 47 / 25 / 16 KB at VERY_HIGH / HIGH / MEDIUM / LOW | Use MEDIUM (~25 KB) for manuals and gauges; HIGH if text is small |
| Mic | PCM or LC3, 8 or 16 kHz, mono/stereo; optional on-device echo cancel + voice band-pass; sound-activity trigger | 16 kHz PCM matches our server's STT/TTS rate [P] |
| Speaker | dual bone conduction, PCM or LC3 at 8/16 kHz on a dedicated **AUDIO TX** BLE characteristic | Our v0 `/ask?audio=pcm` already returns 16 kHz PCM (`pcm_b64`) [P] |
| Input | button single / double / long; IMU tap single / double / triple | Maps to ask / next step / cancel |
| Battery | `frame.battery_level()`, standard BLE battery service | show on companion screen |
| Compute on glasses | Lua 5.4 VM; apps are mostly a host app + a small Lua event loop | logic stays on phone/laptop + our server, same as Mentra |

## 3. How the link works [F, "Talking to Halo Over Bluetooth"]
- **BLE only, bonded pairing required.** Halo pairs with up to 5 hosts, but only **one connection at a time**. Adding a host = hold button 5 s to open a 60 s pairing window.
- Custom service `7A230001-5475-A6A4-654C-8431F6AD49C4`:
  - `…0002` LUA TX (host → Halo): Lua strings, or raw data prefixed `0x01`
  - `…0003` LUA RX (Halo → host, notify): `print()` output and raw data (`0x01` prefix). **Camera JPEG chunks AND mic audio both come back here** (no dedicated characteristic).
  - `…0005` AUDIO TX (host → Halo): speaker PCM/LC3, bypasses Lua for low latency.
- MTU up to 512 bytes; control bytes 0x03 break, 0x04 reset/run main.lua, 0x05 remove main.lua.
- Halo is also a standard **LE Audio** device (mic + speaker over LC3). If a host opens LE Audio, it takes over the mic and `frame.microphone` stops working. Pick one path; the custom path is simpler for us.
- Firmware is open (Zephyr on Alif Balletto; repo `brilliantlabsAR/halo-firmware`, updated Nov 11, 2026) [F].

## 4. What a Halo adapter for our server needs
Our server needs **no change for an MVP** [P]: the adapter is glue, like `mentra_app/`.

| Server call (ours) | Halo side | SDK piece |
|---|---|---|
| `POST /ask` JSON (`question`, `image_b64`, `mode`) → `answer_hud` | capture photo, send question text, draw answer | `TxCaptureSettings` → `RxPhoto` (JPEG bytes) → base64 → `/ask`; draw with `TxTextPage` + `CircularTextLayout` (web) or `frame.display.text()` |
| v0 `POST /ask` multipart (`image` + `audio` WAV) `?audio=pcm` → `hud_text`, `pcm_b64` | send photo + recorded voice, play spoken answer | `RxAudio` (16 kHz PCM) → wrap as WAV → `/ask`; `send_audio()` / `sendAudio()` streams `pcm_b64` to AUDIO TX after `frame.speaker.start{sample_rate=16000}` |
| `POST /guide/next`, `/guide/prev` | double-tap / triple-tap | `RxClick` (0x0B) or `frame.imu.tap_callback` |
| `POST /context` ready card | on connect | draw once, `frame.display.clear()` after 10 s |

**Gap 1, voice on the docs-grounded path:** v1 JSON `/ask` takes text only. Either the adapter does STT itself, or we add an `audio_b64` field to v1 (server already has `stt.transcribe()` in `speech.py`) [P]. Recommend adding it: one server change, every device benefits.

**Gap 2, HUD size:** add a `hud_profile` (e.g. `halo_round`: ~4 lines x ~14 chars, or paginate) so the server shapes text for a 256 px circle instead of clamping in the adapter. Today's example "Normal discharge pressure is 55-70 PSI." is 39 chars ≈ 3 lines at 16 px [EST].

**Latency budget [EST]:** 25 KB photo over BLE at ~30-80 KB/s real throughput ≈ 0.3-0.8 s, plus 2-4 s of speech audio streamed while the worker talks, plus our server round trip. Should land around 2-4 s total; needs measuring on real hardware.

## 5. Three ways to host it (pick by phone)
| Option | Runs on | Effort | Catch |
|---|---|---|---|
| **A. Python bridge** (`pip install brilliant-ble brilliant-msg`, Bleak) | laptop next to the server | lowest, ~1-2 days [EST]; emulator for tests | not wearable in the field; fine for demos and the pilot pitch |
| **B. Web Bluetooth in our `companion/` web app** (`npm brilliant-ble brilliant-msg`) | Chrome/Edge on **Android** or desktop | low-medium, ~2-4 days [EST]; reuses companion UI | **No iPhone** (Safari/Firefox have no Web Bluetooth) [F] |
| **C. Flutter app** (Brilliant Flutter SDK) | iOS + Android | highest, ~1-2 weeks [EST] | production path |
Recommendation: **A now** (works with the emulator before glasses arrive), **B for the Android pilot**, C only if a pilot customer is on iPhones.

## 6. Adapter feasibility verdict
**Feasible, low risk.** Display text, camera capture, mic streaming, speaker playback and button input are all documented, open-source (BSD-3 SDK) and reachable from Python, browser and Flutter. Real risks: (1) Halo delivery delays, (2) the small round display forces a new HUD format, (3) Web Bluetooth excludes iPhones, (4) the BLE photo + audio path is unmeasured.

## Sources
- Store: https://brilliant.xyz/products/halo
- SDK overview: https://docs.brilliant.xyz/halo/halo-sdk/
- Python SDK + emulator: https://docs.brilliant.xyz/halo/halo-sdk-python/
- Web Bluetooth SDK: https://docs.brilliant.xyz/halo/halo-sdk-webbluetooth/
- Lua API (display, camera, mic, speaker): https://docs.brilliant.xyz/halo/halo-sdk-lua/
- BLE spec: https://docs.brilliant.xyz/halo/halo-sdk-bluetooth-specs/
- Repos: https://github.com/brilliantlabsAR (brilliant_sdk, halo-firmware)
- Shipping reports [EX]: https://www.reddit.com/r/SmartGlasses/comments/1xr5ntx/ · https://gamesbeat.com/brilliant-labs-is-betting-on-gamification-to-bring-smart-glasses-to-the-masses/ · https://www.media-factory.net/insights/read/?slug=brilliant-labs-halo-glasses-price-features-reddit
