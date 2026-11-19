/**
 * Typed channels between the background (glasses logic) and the UI WebView
 * (phone settings screen). Shape follows the create-mentra-miniapp template.
 */
import type {Rpc} from "@mentra/miniapp/background"
import type {AskResponse, Config, ContextTrigger, ContextResponse} from "./api"

export interface Channels {
  // UI -> background, request/response
  getConfig: Rpc<Record<string, never>, Config>
  setConfig: Rpc<Partial<Config>, Config>
  askTyped: Rpc<{question: string; mode: "answer" | "guide"}, AskResponse>
  readyCard: Rpc<{trigger: ContextTrigger; label: string}, ContextResponse>
  testServer: Rpc<Record<string, never>, {ok: boolean; message: string}>

  // background -> UI broadcasts (dropped silently when the UI is closed)
  answer: {question: string; response: AskResponse}
  status: {text: string}
}

declare global {
  // eslint-disable-next-line no-var
  var mentra: import("@mentra/miniapp/ui").MentraTyped<Channels>
}
