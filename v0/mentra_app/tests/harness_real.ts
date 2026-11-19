/**
 * Runs the glasses logic (src/background/app.ts) with a FAKE MentraOS session against the REAL answer server
 * (prototype/v0/server, mock LLM, sample workspace "demo", AUTH_MODE=worker). No glasses, phone or Mentra account.
 * Start it with tests/run_harness_real.sh, which starts the server, makes worker tokens, and sets:
 *   REAL_URL, TOKEN_W1, TOKEN_W2, LOG_FILE (the server's asks_v1.jsonl)
 * Every check compares what the fake glasses show with what the server returned or stored (read back over HTTP).
 * Like tests/harness.ts, this proves our flow and request shapes, NOT that the real Mentra runtime behaves like the fake.
 */
import {createWorkGlasses, type GlassesSession, type TextElement} from "../src/background/app.ts"
import {clampHud, HUD_CHARS} from "../src/shared/api.ts"
import * as fs from "node:fs"

const BASE = process.env.REAL_URL ?? "http://127.0.0.1:8000"
const T1 = process.env.TOKEN_W1 ?? ""
const T2 = process.env.TOKEN_W2 ?? ""
const LOG_FILE = process.env.LOG_FILE ?? ""
const results: {name: string; ok: boolean; detail?: string}[] = []
const check = (name: string, ok: boolean, detail: unknown = "") => {
  results.push({name, ok, detail: ok ? undefined : JSON.stringify(detail).slice(0, 400)})
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${ok ? "" : " " + JSON.stringify(detail).slice(0, 400)}`)
}
const tick = (ms = 250) => new Promise((r) => setTimeout(r, ms))
// Wait for a state instead of a fixed time: a real LLM takes 0.5-3 s (and up to the server's 18 s LLM_TIMEOUT).
const until = async (pred: () => boolean, ms = 25000) => { const t0 = Date.now(); while (!pred() && Date.now() - t0 < ms) await tick(100); await tick(50) }
const settled = (x: {hud: () => string[]}) => () => x.hud().length > 0 && x.hud()[0] !== "…"

async function api(method: string, path: string, token: string, body?: unknown): Promise<{status: number; json: any}> {
  const headers: Record<string, string> = {Authorization: `Bearer ${token}`}
  if (body !== undefined) headers["Content-Type"] = "application/json"
  const r = await fetch(BASE + path, {method, headers, body: body === undefined ? undefined : JSON.stringify(body)})
  return {status: r.status, json: await r.json().catch(() => null)}
}

type Handler<T> = (d: T) => void
function fakeSession(opts: {display: boolean; camera: boolean}) {
  const frames: TextElement[][] = []
  const spoken: string[] = []
  const store = new Map<string, string>()
  let btn: Handler<{buttonId: string; pressType: "short" | "long"}> | null = null
  let touch: Handler<{kind: string}> | null = null
  const stt = new Set<Handler<{text: string; isFinal: boolean}>>()
  // 64x48 mid-grey JPEG (bright enough that the mock vision check does not say "Can't read that")
  const JPEG = "data:image/jpeg;base64," + fs.readFileSync(new URL("./grey.jpg.b64", import.meta.url), "utf8").trim()
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
    s, frames, spoken, stt,
    press: (pressType: "short" | "long" = "short") => btn?.({buttonId: "main", pressType}),
    gesture: (kind: string) => touch?.({kind}),
    say: (text: string, isFinal = true) => [...stt].forEach((h) => h({text, isFinal})),
    hud: () => { const f = frames.at(-1); return f && f[0] ? f[0].text.split("\n") : [] },
  }
}
const hudOk = (lines: string[]) => lines.length <= 3 && lines.every((l) => l.replace("…", "").split(/\s+/).filter(Boolean).length <= 7 && l.length <= HUD_CHARS)
const fetchImpl = (url: string, init?: RequestInit) => fetch(url, init) as never

async function main() {
  check("setup: server is up in worker-token mode", (await (await fetch(BASE + "/health")).json()).auth === "worker")
  await api("DELETE", "/memory", T1)

  // ---- display glasses with w1's token
  const g = fakeSession({display: true, camera: false})
  let last: any = null
  const app = createWorkGlasses(g.s, fetchImpl, {serverUrl: BASE, workspaceId: "demo", workerToken: T1}, {onAnswer: (_q, r) => { last = r }})
  const cfg = await app.loadConfig(); app.start()
  check("config: with a token, worker id is left to the server (not the Mentra user id)", cfg.workerId === "", cfg)

  g.press(); g.say("Is this pressure normal?"); await until(() => last !== null && settled(g)())
  check("voice ask -> real /ask -> HUD == clamped server answer_hud", !!last && JSON.stringify(g.hud()) === JSON.stringify(clampHud(last.answer_hud)), [g.hud(), last])
  check("answer cites the manual page (last HUD line 'Source: … p.14')", /^Source: .*p\.14$/.test(g.hud().at(-1) ?? "") && last.sources[0].page === "14", g.hud())
  check("HUD frame obeys 3 lines x 7 words x 40 chars", hudOk(g.hud()), g.hud())
  const m1 = (await api("GET", "/memory", T1)).json
  check("server stored the ask under the token's worker (w1), in workspace demo", m1.worker_id === "w1" && m1.items[0]?.text.startsWith("Q: Is this pressure normal?"), m1)
  const m2 = (await api("GET", "/memory", T2)).json
  check("another worker (w2) does not see it", Array.isArray(m2.items) && m2.items.length === 0, m2)

  // guide mode by voice + swipes + voice commands
  g.press(); g.say("Show me the seal swap step by step"); await tick()
  const total = last?.guide?.total
  check("guide phrase -> real guide, step 1/N copied from the doc, warning first", total >= 2 && g.hud()[0].startsWith("⚠") && g.hud().join(" ").includes(`Step 1/${total}`) && app.state.guideActive, g.hud())
  g.gesture("swipe_up"); await tick()
  check("swipe up -> /guide/next -> step 2", g.hud().join(" ").includes(`Step 2/${total}`) && hudOk(g.hud()), g.hud())
  g.gesture("swipe_down"); await tick()
  check("swipe down -> /guide/prev -> step 1", g.hud().join(" ").includes(`Step 1/${total}`), g.hud())
  g.press(); g.say("Next."); await tick()
  check("tap + say 'next' -> step 2", g.hud().join(" ").includes(`Step 2/${total}`), g.hud())
  for (let i = 2; i <= total; i++) { g.gesture("swipe_up"); await tick(120) }
  check("swiping past the last step -> server finished:true, HUD 'All N steps done'", last?.finished === true && g.hud()[0] === `All ${total} steps done`, [g.hud(), last?.finished])
  g.press(); g.say("Done, log it"); await tick()
  check("'Done, log it' logs the job (memory command)", last?.kind === "memory" && g.hud()[0].startsWith("Logged:"), [g.hud(), last])
  check("server guide closed after logging", (await api("POST", "/guide/next", T1, {})).status === 404)

  // stale guide on the glasses (server side ended) -> clear message, not "can't reach"
  g.press(); g.say("Show me the seal swap step by step"); await tick()
  await api("DELETE", "/memory", T1) // forget-all also ends the server's guide
  g.gesture("swipe_up"); await tick()
  check("guide ended on the server -> HUD says no guide running", g.hud()[0] === "No guide running" && !app.state.guideActive, g.hud())

  // ready card
  const rc = await app.readyCard("see_machine", "CP-450")
  check("ready card (see_machine) == server lines, on the HUD", !!rc && JSON.stringify(g.hud()) === JSON.stringify(clampHud(rc!.lines)) && hudOk(g.hud()), [g.hud(), rc])
  const ctx = (await api("GET", "/memory", T1)).json.items.find((i: any) => i.kind === "context")
  check("ready card call recorded as w1's context", ctx?.text === "see machine: CP-450", ctx)

  // ---- camera glasses (no display), w1
  const live = fakeSession({display: false, camera: true})
  let lastL: any = null
  const appL = createWorkGlasses(live.s, fetchImpl, {serverUrl: BASE, workspaceId: "demo", workerToken: T1}, {onAnswer: (_q, r) => { lastL = r }})
  await appL.loadConfig(); appL.start()
  live.press(); live.say("Is this pressure normal?"); await until(() => live.spoken.length > 0)
  const logLine = LOG_FILE && fs.existsSync(LOG_FILE) ? JSON.parse(fs.readFileSync(LOG_FILE, "utf8").trim().split("\n").at(-1)!) : null
  check("camera glasses: photo reached the real server as image_b64 (server log image=true)", logLine?.image === true && logLine?.worker === "w1", logLine)
  check("no display: answer spoken, nothing rendered", live.spoken.length === 1 && live.spoken[0] === lastL?.answer_full && live.frames.length === 0, live.spoken)

  // ---- auth failures say what to fix
  const bad = fakeSession({display: true, camera: false})
  const appB = createWorkGlasses(bad.s, fetchImpl, {serverUrl: BASE, workspaceId: "demo", workerToken: "wt_typo"}); await appB.loadConfig(); appB.start()
  bad.press(); bad.say("Is this pressure normal?"); await until(settled(bad))
  check("wrong token -> HUD 'Sign-in failed' (not 'can't reach')", bad.hud()[0] === "Sign-in failed", bad.hud())
  const t = await appB.testServer()
  check("testServer reports the 401", !t.ok && t.message.includes("401"), t)
  const imp = fakeSession({display: true, camera: false})
  const appI = createWorkGlasses(imp.s, fetchImpl, {serverUrl: BASE, workspaceId: "demo", workerToken: T1, workerId: "w2"}); await appI.loadConfig(); appI.start()
  imp.press(); imp.say("Is this pressure normal?"); await until(settled(imp))
  check("w1's token claiming worker w2 -> 403 -> HUD 'Not allowed for this worker'", imp.hud()[0] === "Not allowed for this worker", imp.hud())
  const noTok = fakeSession({display: true, camera: false})
  const appN = createWorkGlasses(noTok.s, fetchImpl, {serverUrl: BASE, workspaceId: "demo"}); const cfgN = await appN.loadConfig(); appN.start()
  noTok.press(); noTok.say("Is this pressure normal?"); await until(settled(noTok))
  check("no token (worker id = Mentra user id) -> 401 -> 'Sign-in failed'", cfgN.workerId === "mentra-user-123" && noTok.hud()[0] === "Sign-in failed", noTok.hud())

  // G43: rate limit (429) -> a HUD that says to wait, not "Can't reach answers". Empty questions are refused (422)
  // after the limiter has counted them, so this burns w2's burst without any LLM call.
  for (let i = 0; i < 12; i++) await api("POST", "/ask", T2, {workspace_id: "demo", question: ""})
  const rl = fakeSession({display: true, camera: false})
  const appR = createWorkGlasses(rl.s, fetchImpl, {serverUrl: BASE, workspaceId: "demo", workerToken: T2}); await appR.loadConfig(); appR.start()
  rl.press(); rl.say("Is this pressure normal?"); await until(settled(rl))
  check("rate limited (429) -> HUD 'Too many questions'", rl.hud()[0] === "Too many questions" && hudOk(rl.hud()), rl.hud())

  const passed = results.filter((r) => r.ok).length
  console.log(`${passed}/${results.length} passed`)
  fs.writeFileSync(new URL((process.env.HARNESS_LLM ?? "mock") === "mock" ? "./harness_real_results.json" : `./harness_real_results_${process.env.HARNESS_LLM}.json`, import.meta.url), JSON.stringify({summary: `${passed}/${results.length} passed`, server: `real (${process.env.HARNESS_LLM ?? "mock"} LLM, AUTH_MODE=worker)`, results}, null, 1))
  process.exit(passed === results.length ? 0 : 1)
}
main().catch((e) => { console.error(e); process.exit(1) })
