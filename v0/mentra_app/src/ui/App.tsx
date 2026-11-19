/**
 * Phone-side screen (WebView). No hardware access: everything goes to the
 * background over the typed channels in src/shared/channels.ts.
 * Settings, a typed ask (for testing without speaking), the last answer
 * with sources, and ready-card test buttons.
 */
import {useEffect, useState} from "react"
import {MiniappHeader, useSafeArea} from "@mentra/miniapp/ui"
import type {AskResponse, Config, ContextTrigger} from "../shared/api.ts"
import {clampHud} from "../shared/api.ts"

export function App() {
  const {insets} = useSafeArea()
  const [cfg, setCfg] = useState<Config | null>(null)
  const [status, setStatus] = useState("")
  const [question, setQuestion] = useState("")
  const [last, setLast] = useState<{question: string; response: AskResponse} | null>(null)
  const [hud, setHud] = useState("")

  useEffect(() => {
    void mentra.request("getConfig", {}).then(setCfg)
    const offA = mentra.on("answer", (a) => setLast(a))
    const offS = mentra.on("status", ({text}) => setHud(text))
    return () => { offA(); offS() }
  }, [])

  const save = async () => {
    if (!cfg) return
    setCfg(await mentra.request("setConfig", cfg))
    const t = await mentra.request("testServer", {})
    setStatus(t.message)
  }
  const ask = async (mode: "answer" | "guide") => {
    if (!question.trim()) return
    setStatus("Asking…")
    try { setLast({question, response: await mentra.request("askTyped", {question, mode}, {timeout: 30000})}); setStatus("") }
    catch (e) { setStatus(e instanceof Error ? e.message : String(e)) }
  }
  const card = async (trigger: ContextTrigger) => {
    try { const r = await mentra.request("readyCard", {trigger, label: ""}); setHud(clampHud(r.lines).join("\n")) }
    catch (e) { setStatus(e instanceof Error ? e.message : String(e)) }
  }

  return (
    <div className="app" style={{paddingTop: insets.top, paddingBottom: insets.bottom, paddingLeft: insets.left, paddingRight: insets.right}}>
      <MiniappHeader title="Work Glasses" />
      <main className="content" style={{alignItems: "stretch", textAlign: "left", justifyContent: "flex-start"}}>
        <p>Tap the glasses (or press the button) and ask. Swipe for the next step.</p>

        <section>
          <h3>On the glasses now</h3>
          <pre className="hud">{hud || "—"}</pre>
        </section>

        <section>
          <h3>Ask by typing</h3>
          <input value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="Is this pressure normal?" />
          <div className="row">
            <button type="button" onClick={() => void ask("answer")}>Ask</button>
            <button type="button" className="secondary" onClick={() => void ask("guide")}>Guide me</button>
          </div>
          <div className="row">
            <button type="button" className="secondary" onClick={() => void card("see_machine")}>See machine</button>
            <button type="button" className="secondary" onClick={() => void card("open_job")}>Open job</button>
            <button type="button" className="secondary" onClick={() => void card("start_shift")}>Start shift</button>
          </div>
        </section>

        {last && (
          <section>
            <h3>Last answer</h3>
            <p><b>{last.question}</b></p>
            <p>{last.response.answer_full}</p>
            <ul>
              {last.response.sources.map((s, i) => (
                <li key={i}><b>{s.title}{s.page != null ? ` p.${s.page}` : ""}</b>: {s.snippet}</li>
              ))}
              {!last.response.sources.length && <li>No source: treat as unverified.</li>}
            </ul>
          </section>
        )}

        {cfg && (
          <section>
            <h3>Settings</h3>
            <label>Server URL<input value={cfg.serverUrl} onChange={(e) => setCfg({...cfg, serverUrl: e.target.value})} placeholder="https://…" /></label>
            <label>Workspace<input value={cfg.workspaceId} onChange={(e) => setCfg({...cfg, workspaceId: e.target.value})} /></label>
            <label>Worker ID<input value={cfg.workerId} onChange={(e) => setCfg({...cfg, workerId: e.target.value})} /></label>
            <label>Worker token<input type="password" autoComplete="off" value={cfg.workerToken ?? ""} onChange={(e) => setCfg({...cfg, workerToken: e.target.value})} placeholder="only if the server uses worker tokens" /></label>
            <label className="check"><input type="checkbox" checked={cfg.speakAnswers} onChange={(e) => setCfg({...cfg, speakAnswers: e.target.checked})} /> Also speak answers (phone/glasses audio)</label>
            <button type="button" onClick={() => void save()}>Save and test</button>
          </section>
        )}
        {status && <p className="status">{status}</p>}
      </main>
    </div>
  )
}
