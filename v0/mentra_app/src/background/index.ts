/**
 * Background entry (always-on JS context on the phone, no DOM).
 * Wires the real MentraOS session into createWorkGlasses() and exposes
 * RPC channels for the phone UI (settings, typed ask, ready-card test).
 *
 * Server URL: set it in the phone UI, or at build time with
 *   MENTRA_PUBLIC_SERVER_URL=https://your-server bun run build
 * (build.ts inlines MENTRA_PUBLIC_* values; never put secrets there).
 */
import {registerMiniapp} from "@mentra/miniapp/background"
import type {Channels} from "../shared/channels.ts"
import {createWorkGlasses} from "./app.ts"

declare const process: {env: Record<string, string | undefined>} | undefined
const BUILD_SERVER_URL = typeof process !== "undefined" ? process.env.MENTRA_PUBLIC_SERVER_URL ?? "" : ""

registerMiniapp<Channels>(async (session) => {
  const app = createWorkGlasses(session, (url, init) => fetch(url, init), {serverUrl: BUILD_SERVER_URL}, {
    onAnswer: (question, response) => session.ui.send("answer", {question, response}),
    onStatus: (text) => session.ui.send("status", {text}),
  })

  // Register UI handlers first (fast), per the SDK's startup guidance.
  session.ui.handle("getConfig", () => app.getConfig())
  session.ui.handle("setConfig", (patch) => app.saveConfig(patch))
  session.ui.handle("askTyped", async ({question, mode}) => {
    const r = await app.ask(question, mode)
    if (!r) throw new Error("No answer (server unreachable or busy)")
    return r
  })
  session.ui.handle("readyCard", async ({trigger, label}) => {
    const r = await app.readyCard(trigger, label)
    if (!r) throw new Error("No ready card (server unreachable)")
    return r
  })
  session.ui.handle("testServer", () => app.testServer())

  await app.loadConfig() // local storage only, fast
  app.start()

  // Anticipation v1: show the start-of-shift ready card once the glasses are connected.
  // Not awaited: network calls must stay out of the startup path.
  session.on("ready", () => {
    void app.loadConfig().then(() => app.readyCard("start_shift", ""))
  })
})
