/**
 * Runs the glasses logic (src/background/app.ts) with a FAKE MentraOS session
 * against a running companion mock server. No glasses, phone, or Mentra account.
 *   python3 ../companion/mock_server.py --port 8765 &
 *   NO_PROXY=127.0.0.1 MOCK_URL=http://127.0.0.1:8765 node --experimental-strip-types tests/harness.ts
 * This proves our flow and request shapes, NOT that the real Mentra runtime behaves like the fake.
 */
import {createWorkGlasses, type GlassesSession, type TextElement} from "../src/background/app.ts"
import {bytesToBase64, clampHud, guideCommand, pickMode} from "../src/shared/api.ts"

const BASE = process.env.MOCK_URL ?? "http://127.0.0.1:8765"
const results: {name: string; ok: boolean; detail?: string}[] = []
const check = (name: string, ok: boolean, detail: unknown = "") => {
  results.push({name, ok, detail: ok ? undefined : JSON.stringify(detail).slice(0, 300)})
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${ok ? "" : " " + JSON.stringify(detail).slice(0, 300)}`)
}
const tick = (ms = 150) => new Promise((r) => setTimeout(r, ms))

type Handler<T> = (d: T) => void
function fakeSession(opts: {display: boolean; camera: boolean}) {
  const frames: TextElement[][] = []
  const spoken: string[] = []
  const store = new Map<string, string>()
  let btn: Handler<{buttonId: string; pressType: "short" | "long"}> | null = null
  let touch: Handler<{kind: string}> | null = null
  const stt = new Set<Handler<{text: string; isFinal: boolean}>>()
  const JPEG = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="
  const s: GlassesSession = {
    userId: "mentra-user-123",
    capabilities: {display: opts.display ? {width: 576, height: 288} : null, hasCamera: opts.camera},
    display: {render: (els) => { frames.push(els) }},
    input: {
      onButtonPress: (h) => { btn = h; return () => { btn = null } },
      onTouch: (_g, h) => { touch = h; return () => { touch = null } },
    },
    transcription: {on: (h) => { stt.add(h); return () => { stt.delete(h) } }},
    camera: {takePhoto: async () => ({photoUrl: JPEG})},
    speaker: {speak: async (t) => { spoken.push(t) }, stop: () => {}},
    storage: {get: async (k) => store.get(k) ?? null, set: async (k, v) => { store.set(k, v) }},
  }
  return {
    s, frames, spoken, store, stt,
    press: (pressType: "short" | "long" = "short") => btn?.({buttonId: "main", pressType}),
    gesture: (kind: string) => touch?.({kind}),
    say: (text: string, isFinal = true) => [...stt].forEach((h) => h({text, isFinal})),
    hud: () => { const f = frames.at(-1); return f && f[0] ? f[0].text.split("\n") : [] },
  }
}
const hudOk = (lines: string[]) => lines.length <= 3 && lines.every((l) => l.replace("…", "").split(/\s+/).filter(Boolean).length <= 7)
const fetchImpl = (url: string, init?: RequestInit) => fetch(url, init) as never

async function main() {
  await fetch(BASE + "/__mock/reset", {method: "POST", body: "{}"})

  // pure helpers
  check("helpers: clampHud enforces 3 lines x 7 words", JSON.stringify(clampHud(["a b c d e f g h i", "2", "3", "4"])) === JSON.stringify(["a b c d e f g…", "2", "3"]))
  check("helpers: bytesToBase64 matches Node", bytesToBase64(new TextEncoder().encode("glasses ✓").buffer as ArrayBuffer) === Buffer.from("glasses ✓").toString("base64"))
  check("helpers: pickMode / guideCommand", pickMode("show me the seal swap") === "guide" && pickMode("is this pressure normal") === "answer" && guideCommand("Next.") === "next" && guideCommand("go back") === "prev" && guideCommand("next week's PM schedule") === null)

  // ---- display glasses (G2-like): voice ask
  const g = fakeSession({display: true, camera: false})
  const app = createWorkGlasses(g.s, fetchImpl, {serverUrl: BASE})
  const cfg = await app.loadConfig(); app.start()
  check("config: worker id defaults to the Mentra user id", cfg.workerId === "mentra-user-123", cfg)
  check("privacy: mic not subscribed before a tap", g.stt.size === 0)
  g.press()
  check("tap arms: HUD says Listening, mic subscribed", g.hud()[0] === "Listening…" && g.stt.size === 1, g.hud())
  g.say("Is this pressure", false); await tick(50)
  check("interim transcript ignored", g.stt.size === 1 && g.hud()[0] === "Listening…")
  g.say("Is this pressure normal?"); await tick()
  check("final transcript -> /ask -> HUD answer", g.hud()[0]?.startsWith("CP-450 normal"), g.hud())
  check("HUD frame obeys 3 lines x 7 words, 500x220 box, maxLines 3", hudOk(g.hud()) && g.frames.at(-1)![0].box.w === 500 && g.frames.at(-1)![0].style?.maxLines === 3, g.frames.at(-1))
  check("privacy: mic unsubscribed after the question", g.stt.size === 0)
  check("display glasses: answer not spoken by default", g.spoken.length === 0)
  const last1 = await (await fetch(BASE + "/__mock/last")).json() as Record<string, unknown>
  check("request shape: workspace, worker, question, mode=answer, no image", last1.workspace_id === "default" && last1.worker_id === "mentra-user-123" && last1.mode === "answer" && !last1.image_b64, last1)

  // over-long server reply is clamped on the glasses
  g.press(); g.say("long test"); await tick()
  check("over-long server HUD clamped before render", hudOk(g.hud()) && g.hud()[0].endsWith("…"), g.hud())

  // guide mode by voice + swipes + voice commands
  g.press(); g.say("Show me the seal swap step by step"); await tick()
  check("guide phrase -> guide mode step 1/6", g.hud()[0]?.includes("Step 1/6") && app.state.guideActive, g.hud())
  g.gesture("swipe_up"); await tick()
  check("swipe up -> next step", g.hud()[0]?.includes("Step 2/6"), g.hud())
  g.gesture("swipe_down"); await tick()
  check("swipe down -> previous step", g.hud()[0]?.includes("Step 1/6"), g.hud())
  g.press(); g.say("Next."); await tick()
  check("tap + say 'next' -> next step", g.hud()[0]?.includes("Step 2/6"), g.hud())
  g.press(); g.say("stop"); await tick()
  check("tap + say 'stop' -> guide ends, display cleared", !app.state.guideActive && g.frames.at(-1)!.length === 0)

  // long press clears and closes the mic
  g.press(); g.press("long"); await tick(30)
  check("long press clears display and closes mic", g.frames.at(-1)!.length === 0 && g.stt.size === 0)

  // ready card
  const rc = await app.readyCard("see_machine", "CP-450")
  check("ready card (see_machine) shown on HUD", !!rc && g.hud()[0]?.includes("seal done") && hudOk(g.hud()), g.hud())

  // settings persist in session.storage
  await app.saveConfig({workspaceId: "plant-7"})
  const g2 = fakeSession({display: true, camera: false}); for (const [k, v] of g.store) g2.store.set(k, v)
  const app2 = createWorkGlasses(g2.s, fetchImpl, {serverUrl: BASE}); const cfg2 = await app2.loadConfig()
  check("settings persist via session.storage", cfg2.workspaceId === "plant-7" && cfg2.serverUrl === BASE, cfg2)

  // ---- camera glasses, no display (Mentra Live-like)
  const live = fakeSession({display: false, camera: true})
  const appL = createWorkGlasses(live.s, fetchImpl, {serverUrl: BASE}); await appL.loadConfig(); appL.start()
  live.press(); live.say("Is this pressure normal?"); await tick(300)
  const last2 = await (await fetch(BASE + "/__mock/last")).json() as Record<string, unknown>
  check("camera glasses: photo sent as image_b64", typeof last2.image_b64 === "number" && (last2.image_b64 as number) > 100, last2)
  check("no display: answer spoken, nothing rendered", live.spoken.length === 1 && live.spoken[0].includes("55-70 PSI") && live.frames.length === 0, {spoken: live.spoken, frames: live.frames.length})

  // ---- server down
  const d = fakeSession({display: true, camera: false})
  const appD = createWorkGlasses(d.s, fetchImpl, {serverUrl: "http://127.0.0.1:9"}); await appD.loadConfig(); appD.start()
  d.press(); d.say("Is this pressure normal?"); await tick(500)
  check("server unreachable -> HUD error, no crash", d.hud()[0] === "Can't reach answers", d.hud())
  const t = await appD.testServer()
  check("testServer reports failure", !t.ok, t)
  const noUrl = fakeSession({display: true, camera: false})
  const appN = createWorkGlasses(noUrl.s, fetchImpl, {}); await appN.loadConfig(); appN.start()
  noUrl.press(); noUrl.say("hello"); await tick()
  check("no server URL -> tells worker to set it in the phone app", noUrl.hud()[1] === "Set server in phone app", noUrl.hud())

  const passed = results.filter((r) => r.ok).length
  console.log(`${passed}/${results.length} passed`)
  const fs = await import("node:fs")
  fs.writeFileSync(new URL("./harness_results.json", import.meta.url), JSON.stringify({summary: `${passed}/${results.length} passed`, results}, null, 1))
  process.exit(passed === results.length ? 0 : 1)
}
main().catch((e) => { console.error(e); process.exit(1) })
