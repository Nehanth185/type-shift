/**
 * Glasses-side logic, written against the small slice of the MentraOS session
 * we use (see GlassesSession). index.ts passes the real session in; the Node
 * harness in tests/ passes a fake one, so the flow can be checked without
 * glasses, a phone, or a Mentra account.
 *
 * Interaction (designed for gloves and noise):
 *   tap / short button press -> "Listening…"; the next finished sentence is the question.
 *                               The mic subscription exists only while armed (8 s window).
 *   question contains "step by step", "guide me", "show me how"…  -> guide mode
 *   swipe up / swipe down, or say "next" / "back" after a tap      -> guide next / prev
 *   long press / double tap                                         -> clear the display
 *   camera glasses (Mentra Live, no display)                        -> one photo per question, answer spoken
 */
import {AnswerClient, ServerError, bytesToBase64, clampHud, guideCommand, pickMode} from "../shared/api.ts"
import type {AskResponse, Config, ContextResponse, ContextTrigger, Mode} from "../shared/api.ts"

export interface TextElement {
  type: "text"
  id: string
  box: {x: number; y: number; w: number; h: number}
  text: string
  style?: {maxLines?: number; overflow?: "clip" | "ellipsis"; breakMode?: "word"}
}

/** The subset of TypedMiniappSession this app uses (structural, so the real session fits). */
export interface GlassesSession {
  readonly userId: string
  readonly capabilities: {display?: {width?: number; height?: number} | null; [key: string]: unknown} | null
  display: {render(elements: TextElement[], options?: {durationMs?: number}): unknown}
  input: {
    onButtonPress(handler: (data: {buttonId: string; pressType: "short" | "long"}) => void): () => void
    onTouch(gestures: string[], handler: (data: {kind: string}) => void): () => void
  }
  transcription: {on(handler: (data: {text: string; isFinal: boolean}) => void): () => void}
  camera: {takePhoto(options?: {size?: "low" | "medium" | "high" | "max"; compress?: "none" | "low" | "medium" | "high"; sound?: boolean}): Promise<{photoUrl: string}>}
  speaker: {speak(text: string): Promise<unknown>; stop(): void}
  storage: {get(key: string): Promise<string | null>; set(key: string, value: string): Promise<void>}
}

type Fetch = ConstructorParameters<typeof AnswerClient>[1]
export const LISTEN_WINDOW_MS = 8000
const CONFIG_KEY = "config.v1"
// Design for 500 x 220 (Mentra's recommended box that fits every positioning display).
const DEFAULT_BOX = {x: 0, y: 0, w: 500, h: 220}

export interface Hooks {
  onAnswer?: (question: string, response: AskResponse) => void
  onStatus?: (text: string) => void
}

export function createWorkGlasses(session: GlassesSession, fetchImpl: Fetch, defaults: Partial<Config> = {}, hooks: Hooks = {}) {
  let cfg: Config = {serverUrl: "", workspaceId: "default", workerId: "", speakAnswers: false, ...defaults}
  const client = new AnswerClient(() => cfg, fetchImpl)
  let armedOff: (() => void) | null = null
  let armTimer: ReturnType<typeof setTimeout> | null = null
  let guideActive = false
  let busy = false

  const hasDisplay = () => !!session.capabilities?.display
  const hasCamera = () => session.capabilities?.hasCamera === true
  const box = () => {
    const d = session.capabilities?.display
    return d?.width && d?.height ? {x: 0, y: 0, w: Math.min(d.width, 500), h: Math.min(d.height, 220)} : DEFAULT_BOX
  }

  function show(lines: string[], durationMs?: number) {
    const text = clampHud(lines).join("\n")
    hooks.onStatus?.(text)
    if (!hasDisplay()) return
    session.display.render(
      [{type: "text", id: "hud", box: box(), text, style: {maxLines: 3, overflow: "ellipsis", breakMode: "word"}}],
      durationMs ? {durationMs} : undefined,
    )
  }
  const clear = () => {
    disarm()
    guideActive = false
    if (hasDisplay()) session.display.render([])
    session.speaker.stop()
  }

  async function loadConfig() {
    const raw = await session.storage.get(CONFIG_KEY)
    if (raw) {
      try { cfg = {...cfg, ...(JSON.parse(raw) as Partial<Config>)} } catch { /* keep defaults */ }
    }
    // With a worker token the server decides who we are; otherwise the Mentra user id is our worker id.
    if (!cfg.workerId && !cfg.workerToken) cfg.workerId = session.userId || "worker"
    return cfg
  }
  async function saveConfig(patch: Partial<Config>) {
    cfg = {...cfg, ...patch}
    await session.storage.set(CONFIG_KEY, JSON.stringify(cfg))
    return cfg
  }

  async function photoB64(): Promise<string | undefined> {
    if (!hasCamera()) return undefined
    try {
      const photo = await session.camera.takePhoto({size: "medium", compress: "medium", sound: true})
      const res = await fetch(photo.photoUrl) // signed URL, ~30 min TTL; we never store it
      return bytesToBase64(await res.arrayBuffer())
    } catch {
      return undefined // ask without a photo rather than fail the question
    }
  }

  function present(question: string, r: AskResponse) {
    guideActive = !!r.guide && r.guide.step <= r.guide.total
    show(r.answer_hud?.length ? r.answer_hud : ["No answer"])
    if (!hasDisplay() || cfg.speakAnswers) void session.speaker.speak(r.answer_full || r.answer_hud.join(". ")).catch(() => {})
    hooks.onAnswer?.(question, r)
  }
  function fail(e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    if (e instanceof ServerError && e.status === 401) return show(["Sign-in failed", "Check worker token in phone app"])
    if (e instanceof ServerError && e.status === 403) return show(["Not allowed for this worker", "Check worker ID or workspace"])
    if (e instanceof ServerError && e.status === 429) return show(["Too many questions", "Wait a few seconds"]) // G43: was "Can't reach answers"
    if (e instanceof ServerError && e.status === 404 && guideActive) { guideActive = false; return show(["No guide running", "Ask again in guide mode"]) }
    show(["Can't reach answers", msg.includes("No server") ? "Set server in phone app" : "Check Wi-Fi and server"])
  }

  async function ask(question: string, mode: Mode = pickMode(question)) {
    if (busy || !question.trim()) return
    busy = true
    show(["…", question.slice(0, 40)])
    try {
      const img = await photoB64()
      const r = await client.ask(question, mode, img)
      present(question, r)
      return r
    } catch (e) { fail(e) } finally { busy = false }
  }

  async function guide(dir: "next" | "prev") {
    if (!guideActive || busy) return
    busy = true
    try { present(`guide ${dir}`, await client.guide(dir)) } catch (e) { fail(e) } finally { busy = false }
  }

  async function readyCard(trigger: ContextTrigger, label: string): Promise<ContextResponse | undefined> {
    try {
      const r = await client.context(trigger, label)
      if (r.lines?.length) show(r.lines, 10000)
      return r
    } catch { return undefined } // a missing ready card is not worth an error on the HUD
  }

  function disarm() {
    if (armTimer) clearTimeout(armTimer)
    armTimer = null
    armedOff?.()
    armedOff = null
  }
  /** Open the mic for one sentence. Nothing is transcribed outside this window. */
  function arm() {
    if (busy) return
    disarm()
    show(guideActive ? ["Listening…", "Say: next / back / stop", "or ask a new question"] : ["Listening…", "Ask your question"])
    armedOff = session.transcription.on((data) => {
      if (!data.isFinal || !data.text.trim()) return
      const text = data.text.trim()
      disarm()
      const cmd = guideActive ? guideCommand(text) : null
      if (cmd === "exit") { clear(); return }
      if (cmd) { void guide(cmd); return }
      void ask(text)
    })
    armTimer = setTimeout(() => { disarm(); if (!busy) show([]) }, LISTEN_WINDOW_MS)
  }

  function start() {
    session.input.onButtonPress((p) => (p.pressType === "long" ? clear() : arm()))
    session.input.onTouch(["single_tap", "double_tap", "swipe_up", "swipe_down"], (t) => {
      if (t.kind === "single_tap") arm()
      else if (t.kind === "double_tap") clear()
      else if (t.kind === "swipe_up") void guide("next")
      else if (t.kind === "swipe_down") void guide("prev")
    })
  }

  return {
    start, loadConfig, saveConfig, ask, guide, readyCard, arm, clear,
    getConfig: () => cfg,
    testServer: async () => {
      try { await client.ping(); return {ok: true, message: `Connected to ${cfg.serverUrl}`} } catch (e) { return {ok: false, message: e instanceof Error ? e.message : String(e)} }
    },
    get state() { return {guideActive, busy, armed: !!armedOff} },
  }
}
