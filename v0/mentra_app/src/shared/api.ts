/**
 * Client for our answer server (prototype/v0/server). Pure functions only:
 * no SDK imports, so the same code runs in the MentraOS background context,
 * the UI WebView, and the Node test harness.
 *
 * Background-runtime limits (docs.mentraglass.com, two-layer architecture):
 * fetch with string bodies, atob/btoa, timers, AbortController subset.
 * No Buffer, no XMLHttpRequest, no FormData guarantees -> JSON + base64 only.
 */

export type Mode = "answer" | "guide"
export type ContextTrigger = "see_machine" | "open_job" | "start_shift"

/** page is the printed page label from the doc ("14", "iv"), a string on the real server. */
export interface Source {doc_id: string; title: string; page: string | number | null; snippet: string}
export interface GuideState {step: number; total: number; text: string}
export interface AskResponse {
  answer_hud: string[]
  answer_full: string
  sources: Source[]
  guide?: GuideState | null
  latency_ms?: number
  mock?: boolean
  // optional extras from the real server (API.md): safe to ignore
  finished?: boolean
  not_in_docs?: boolean
  ungrounded?: boolean
  guide_available?: boolean
  kind?: "memory"
  error?: string
}
export interface ContextResponse {
  lines: string[]
  last_answer: string | null
  usual_next_step: string | null
  unfinished: string[]
  trigger?: ContextTrigger
  label?: string
}
/** workerToken: per-worker token from the server admin (server AUTH_MODE=worker). When set, the server decides
 *  who the worker is, so workerId can stay empty (it is then left out of requests). */
export interface Config {serverUrl: string; workspaceId: string; workerId: string; speakAnswers: boolean; workerToken?: string}

export const HUD_LINES = 3
export const HUD_WORDS = 7
export const HUD_CHARS = 40 // = server HUD_CHARS

/** Enforce the one-eye HUD rule client-side: max 3 lines, max 7 words and 40 characters each. */
export function clampHud(lines: readonly unknown[] | null | undefined): string[] {
  return (lines ?? []).slice(0, HUD_LINES).map((l) => {
    const w = String(l ?? "").trim().split(/\s+/).filter(Boolean)
    let text = w.slice(0, HUD_WORDS).join(" ")
    let cut = w.length > HUD_WORDS
    if (text.length > HUD_CHARS) { text = text.slice(0, HUD_CHARS - 1); cut = true }
    return cut ? text.replace(/[\s.,;:…]+$/, "") + "…" : text
  })
}

/** Error from our server, with the HTTP status kept so the HUD can say what to fix. */
export class ServerError extends Error {
  status: number
  constructor(status: number, detail: string) {
    super(`Server error ${status}: ${detail}`)
    this.status = status
  }
}

/** ArrayBuffer -> base64 using only btoa (no Buffer in the background runtime). */
export function bytesToBase64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf)
  let bin = ""
  const CHUNK = 0x8000
  for (let i = 0; i < bytes.length; i += CHUNK) {
    bin += String.fromCharCode.apply(null, Array.from(bytes.subarray(i, i + CHUNK)))
  }
  return btoa(bin)
}

/** Words that switch a spoken question into step-by-step guide mode. */
export function pickMode(question: string): Mode {
  return /\b(step by step|guide me|walk me through|show me (how|the)|how do i (replace|swap|change|install|remove))\b/i.test(question)
    ? "guide"
    : "answer"
}

/** Voice commands accepted while a guide is running (only heard while armed). */
export function guideCommand(text: string): "next" | "prev" | "exit" | null {
  const t = text.trim().toLowerCase().replace(/[.!?]+$/, "")
  if (/^(next|next step|done|ok next)$/.test(t)) return "next"
  if (/^(back|previous|go back|last step)$/.test(t)) return "prev"
  if (/^(stop|exit|stop guide|cancel)$/.test(t)) return "exit"
  return null
}

type FetchLike = (input: string, init?: {method?: string; headers?: Record<string, string>; body?: string; signal?: AbortSignal}) => Promise<{ok: boolean; status: number; json(): Promise<unknown>; text(): Promise<string>}>

export class AnswerClient {
  // (no TS parameter properties: keeps this file runnable with node --experimental-strip-types)
  private cfg: () => Config
  private fetchImpl: FetchLike
  private timeoutMs: number
  constructor(cfg: () => Config, fetchImpl: FetchLike, timeoutMs = 20000) {
    this.cfg = cfg
    this.fetchImpl = fetchImpl
    this.timeoutMs = timeoutMs
  }

  private async call<T>(method: string, path: string, body?: unknown): Promise<T> {
    const base = this.cfg().serverUrl.replace(/\/+$/, "")
    if (!base) throw new Error("No server URL set")
    const ctl = typeof AbortController !== "undefined" ? new AbortController() : null
    const timer = ctl ? setTimeout(() => ctl.abort(), this.timeoutMs) : null
    try {
      const headers: Record<string, string> = body === undefined ? {} : {"Content-Type": "application/json"}
      const token = (this.cfg().workerToken ?? "").trim()
      if (token) headers["Authorization"] = `Bearer ${token}`
      const res = await this.fetchImpl(base + path, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: ctl?.signal,
      })
      if (!res.ok) throw new ServerError(res.status, (await res.text()).slice(0, 160))
      return (await res.json()) as T
    } finally {
      if (timer) clearTimeout(timer)
    }
  }

  /** worker_id only when we have one: with a worker token the server fills it in. */
  private worker(): Record<string, string> {
    const w = (this.cfg().workerId ?? "").trim()
    return w ? {worker_id: w} : {}
  }
  ask(question: string, mode: Mode, imageB64?: string): Promise<AskResponse> {
    const c = this.cfg()
    const body: Record<string, unknown> = {workspace_id: c.workspaceId, ...this.worker(), question, mode}
    if (imageB64) body.image_b64 = imageB64
    return this.call<AskResponse>("POST", "/ask", body)
  }
  guide(dir: "next" | "prev"): Promise<AskResponse> {
    return this.call<AskResponse>("POST", `/guide/${dir}`, {...this.worker()})
  }
  context(trigger: ContextTrigger, label: string): Promise<ContextResponse> {
    return this.call<ContextResponse>("POST", "/context", {...this.worker(), trigger, label})
  }
  async ping(): Promise<boolean> {
    await this.call<unknown>("GET", `/docs?workspace_id=${encodeURIComponent(this.cfg().workspaceId)}`)
    return true
  }
}
