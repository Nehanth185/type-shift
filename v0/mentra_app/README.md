# Work Glasses Answers: MentraOS miniapp (skeleton v0)

A MentraOS **miniapp** that sends your spoken question (plus a photo, on camera glasses) to our answer server's `POST /ask` and shows `answer_hud` (≤3 lines × ≤7 words) on the glasses display. It is about 400 lines of TypeScript glue. All the product logic stays in our own server, so we can drop Mentra in a week if we need to.

**Tags:** [F] = from a page I opened today (2026-11-11), URLs at the bottom · [P] = our project files · [E] = my estimate · [V] = verified by running it here.

## What it does
| Glasses action | Result |
|---|---|
| Tap the touchpad or short-press the button | HUD shows "Listening…". The **next finished sentence** is the question. The mic subscription exists only for this 8-second window: no always-on listening (privacy rule in SPEC §5) |
| Question contains "step by step", "guide me", "show me how/the…" | `mode:"guide"`: HUD shows step 1/N |
| Swipe up / swipe down, or tap + say "next" / "back" / "stop" | `POST /guide/next` / `prev`, or end the guide |
| Long press, or double tap | Clear the display, close the mic |
| Glasses connect | Start-of-shift ready card from `POST /context` (shown 10 s) |
| Camera glasses (Mentra Live) | One photo per question (`takePhoto` → signed URL → bytes → base64 `image_b64`). Answer is **spoken**, since Live has no display |
| Server down / no URL | HUD: "Can't reach answers" + what to fix |

The phone screen (WebView) has settings (server URL, workspace, worker ID, **worker token**, "also speak answers"), a typed ask, the last answer with sources, and ready-card test buttons.

## Files
| Path | What |
|---|---|
| `miniapp.json` | Manifest: package `com.workglasses.answers`; permissions MICROPHONE, CAMERA; hardware DISPLAY optional, MICROPHONE required, CAMERA/BUTTON optional |
| `src/background/index.ts` | Entry: `registerMiniapp<Channels>(…)`, UI RPC handlers, ready card on `ready` |
| `src/background/app.ts` | Glasses logic written against a small `GlassesSession` interface, so it can be tested with a fake session |
| `src/shared/api.ts` | Our server client + `clampHud`, `bytesToBase64` (btoa only), `pickMode`, `guideCommand`. No SDK imports |
| `src/shared/channels.ts` | Typed UI ↔ background channels (`Rpc<…>` for request/response) |
| `src/ui/*` | React phone screen (`MentraProvider`, `MiniappHeader`, `useSafeArea`) |
| `build.ts`, `tsconfig.json`, `icon.png`, `.gitignore` | Copied from the official `create-mentra-miniapp@0.1.0-beta.2` template. tsconfig changed: `noEmit` + `allowImportingTsExtensions` (Bun does the build) |
| `tests/harness.ts` | Runs `app.ts` with a **fake** session against `../companion/mock_server.py` |
| `tests/harness_real.ts`, `tests/run_harness_real.sh` | Same fake session against the **real** server (mock LLM, sample workspace, worker tokens). The script starts the server, makes tokens for w1/w2, runs the harness |

## What was verified here [V]
| Check | Result |
|---|---|
| Typecheck against the real SDK types (`@mentra/miniapp@3.4.0-beta.1237`, the version the beta scaffolder pins) | `tsc --noEmit`: **0 errors** |
| Production build with the template's `build.ts` (Bun 1.4.4) | OK: `dist/background/index.js` (185 KB IIFE), `dist/ui/*` |
| `mentra-miniapp pack` (official CLI 0.1.0-beta.2; validates the manifest) | OK: `com.workglasses.answers-0.1.0.zip`, unsigned. Only warning: placeholder icon |
| Fake-session harness vs MOCK server | **26/26 passed** (`tests/harness_results.json`, 2026-11-12) |
| Fake-session harness vs the **REAL** server (`AUTH_MODE=worker`) | **23/23 passed** (`tests/harness_real_results.json`, 2026-11-12): voice ask → HUD == server answer with `p.14` source; ask stored under the token's worker, invisible to w2; guide by voice/swipes to `finished`, "Done, log it"; guide ended server-side → "No guide running"; ready card == server lines; camera photo reaches the server; 401 → "Sign-in failed", 403 → "Not allowed for this worker" |
| Typecheck re-run 2026-11-12 after the token changes | `tsc --noEmit`: **0 errors**. Bun build and `pack` were **not** re-run (no Bun in this sandbox) |

The harness proves our flow and request shapes. It does **not** prove that the real Mentra runtime behaves like my fake.

## Setup steps (for when you have glasses + a phone)
1. Install [Bun](https://bun.sh) on the laptop.
2. Install the **Mentra App** on the phone (iOS or Android) from mentraglass.com/os and **sign in** [F quickstart]. ⚠ This needs a **Mentra account**. I did not create one. Mentra's terms need **parental consent for users aged 13-18** [P, MENTRA_VS_US S10], so this is your call and a parent's.
3. Pair the glasses in the Mentra App.
4. On the laptop:
   ```bash
   cd prototype/v0/mentra_app
   bun install
   MENTRA_PUBLIC_SERVER_URL=https://<your-answer-server> bun dev   # prints a QR code
   ```
   Phone and laptop on the **same Wi-Fi**, or on Android plug in and use `bun dev --usb` [F].
5. Mentra App → **Settings → Miniapp Developer Settings → Scan Miniapp QR Code** [F]. The app loads and hot-reloads on save.
6. To run it without the laptop: `bun run release` and scan that QR. It installs on the phone and keeps working offline from the laptop [F distribution].
7. Without glasses you can still run it: the docs say the display "just won't have anywhere to draw" [F]. The typed ask on the phone screen still hits `/ask`.

Checks without hardware: `bun run typecheck`; `node --experimental-strip-types tests/harness.ts` with the mock server running (see the header of `tests/harness.ts`).

## What needs a Mentra account or a key
| Thing | Needed? | Notes |
|---|---|---|
| Mentra account (sign in to the Mentra App) | **Yes**, to load anything on a phone | Not created. Needs your OK, plus parental consent (under 18) |
| Developer API key | **No**, not for the Miniapp SDK as documented | Dev mode and LAN release installs use a QR code. The old cloud SDK (`@mentra/sdk`) is retired, and its docs moved to mentraglass.com/legacy [F] |
| Publisher signing key | Optional | Made **locally** with `mentra-miniapp keys create`. Signing is **permanent per package**: once a phone installs a signed build, it only accepts that key [F CLI]. Don't sign until we're sure |
| Developer Console / Miniapp Store | **Not available** for Miniapp SDK apps in MentraOS 3.0: "There is currently no way to distribute a miniapp" [F] | So this can only reach phones we scan ourselves |
| Company workspace on the phone | Maybe | Dev tools only show if the workspace allows miniapp development [F] |
| Worker token from our server | Only if the server runs `AUTH_MODE=worker` | Made with `python workers.py add <id>` on the server, pasted in the phone settings. Sent as `Authorization: Bearer`; then `worker_id` is left out and the server takes it from the token [V] |
| `session.auth` (Mentra-signed user token to our server) | Not used yet | Later alternative to pasting a token: verify with `@mentra/auth` on our server [F auth] |
| Glasses | Must be **bought**: G2 $599, Z100 ~$500, Live $449 [P] | Spending needs your OK |

## Which glasses it would run on
| Glasses | Display | Camera | Mic | How our app behaves |
|---|---|---|---|---|
| **Even Realities G2** | Yes, text + images, 576×288, positioned [F] | No | Yes | **Best fit**: voice question → 3-line HUD. No photo |
| Even Realities G1 | Yes, 640×200, **text fallback** (no positioning, no images) [F] | No | Yes | Same, plain text |
| Vuzix Z100 | Yes, 640×480, text fallback [F] | No | **Phone mic** [F] | Same, but you speak into the phone |
| Mentra Mach 1, Xingyi AR99 | Text only [F] | No | Partial / yes [F] | Should work as text (untested) |
| NIMO | 500×220, "coming soon" [F] | | | Our 500×220 box was chosen for this and Mentra's recommendation |
| **Mentra Live** | **None** [F] | Yes, 12 MP [P] | Yes | Photo + spoken answer, no HUD. Mentra **recommends their Bluetooth SDK, not the Miniapp SDK, for Live** [F] |

**The gap that matters:** no glasses MentraOS supports have **both a display and a camera** [F compatibility matrix]. Our core loop is "look → ask → read". On Mentra hardware that means voice-only questions on a display pair, or photo + voice answer on Live. The phone camera from the WebView is a possible workaround, but I haven't checked that the WebView allows `getUserMedia`.

## Platform risk (read before building on this)
1. **Beta SDK.** Every docs page says "APIs may change before general availability" [F]. Versions are pinned; expect breakage. The stable tag is 3.3.0, and the scaffolder pins 3.4.0-beta.1237 [F npm].
2. **No distribution.** No store or console for Miniapp SDK apps in 3.0 [F]. Fine for demos, not for pilots.
3. **Track record.** Mentra shut down its whole cloud SDK in 2026 (ended Aug 3, legacy until Oct) and rewrote the OS for 3.0 [P, MENTRA_VS_US S14/S15]. Apps built on it had to be ported.
4. **They're a competitor.** Mentra sells to the same frontline buyers, and "look up" is on their roadmap [P]. They can see what works on their platform [E].
5. **Mitigation built in:** all answers, memory, and docs live on our server. `src/shared/api.ts` is the only coupling, and the same contract feeds the companion web app and our own v0 glasses. Moving off Mentra means rewriting `src/background/` (about 230 lines) [E].

## Not verified (needs a phone, glasses, or an account)
- Nothing has run inside the real Mentra App or on any glasses.
- Whether background `fetch` may call **plain `http://` on a LAN IP** (iOS ATS / Android cleartext rules). Use an **HTTPS** server URL to be safe.
- Latency of subscribing to transcription on each tap. If the first words get cut, keep the subscription open while the app is in the foreground instead (a privacy trade-off).
- That `takePhoto().photoUrl` can be fetched as bytes from the background context.
- How G1/Z100 wrap our 3 lines (the host does the wrapping).
- Whether a manifest that requests CAMERA installs cleanly on glasses without a camera (declared OPTIONAL, which should mean "degraded" [F]).
- Real `answer_hud` from our server is now checked (mock LLM provider, fake session). A real LLM's answers are not.
- **Privacy caveat:** on Live, `takePhoto` uploads the photo through Mentra's service and returns a signed URL that lasts about 30 min [F photos]. Our app doesn't keep it, but the photo does pass through Mentra. That matters for the "no photos saved" promise in SPEC §5.

## Sources (opened 2026-11-11)
- Docs index: https://docs.mentraglass.com/llms.txt
- Overview: https://docs.mentraglass.com/app-devs/getting-started/overview.md
- Quickstart: https://docs.mentraglass.com/app-devs/getting-started/quickstart.md
- Distribution: https://docs.mentraglass.com/app-devs/getting-started/distribution.md
- Two-layer architecture (background runtime APIs): https://docs.mentraglass.com/app-devs/core-concepts/two-layer-architecture.md
- Manifest: https://docs.mentraglass.com/app-devs/core-concepts/miniapp-manifest.md
- Session, Display, Transcription, Input, Photos, Speaker, Storage, Auth, CLI: `https://docs.mentraglass.com/app-devs/core-concepts/…` and `/app-devs/reference/cli.md`
- Compatibility matrix: https://github.com/Mentra-Community/MentraOS/blob/dev/glasses-compatibility.md
- Repo: https://github.com/Mentra-Community/MentraOS (Apache-2.0)
- npm: `@mentra/miniapp`, `@mentra/miniapp-cli`, `create-mentra-miniapp` (template files read from the published tarballs)
- Raw copies of the docs pages: `web/notes/mentra_sdk/`
