# Real-model results: Nemotron 3.5 Super VL on NVIDIA NIM (2026-11-12)

First time the v0 server ran against a real model. Model: `nvidia/nemotron-3.5-super-vl-120b-a12b` via `integrate.api.nvidia.com` (free tier), `reasoning_effort: none`. Local only: nothing published, no accounts, nothing spent. About 250 NIM calls in total. That count is my **estimate** from the run logs; it was not metered.

Everything below is **measured** unless it says *estimate*. Grading is my own judgment, with no second rater.

## Headline

| What | Result |
|---|---|
| Grounded answers over our docs (final code) | **17/19** right fact + right page, **11/11** "not in your docs" refusals, **0** unflagged hallucinations |
| Same refusals with the retrieval gate turned off (the model alone) | **11/11** refused or answered from the photo exactly |
| Latency per model answer (119 calls, all runs pooled) | **median 0.64 s, p90 1.59 s**, max 8.3 s. Text only: median 0.64 s, p90 1.15 s. With a photo: 1.4–2.1 s (one call took 8.3 s) |
| Vision concept test (15 photos) | **Nemotron 70%**, Llama 3.2 90B 63%, SmolVLM best 33% |
| Companion app vs real server + real model | **80/80** (42 worker-token + 38 no-auth) |
| Mentra harness vs real server + real model | **23/23** |
| Mock suites after all fixes | pytest **105/105**, v0 loop **11/11**, companion vs real server (mock LLM) **80/80**, companion vs mock **36/36**, Mentra real **23/23**, Mentra mock **26/26**, `tsc` 0 errors |

**Biggest risk found:** Nemotron sometimes **makes up analog gauge readings**. On a gauge sitting at 75 °F / 0 psi it said "about 60 PSI" in 2 of 7 grounded runs, and gave 3 different readings in 3 vision runs. The server now flags any number that isn't in the docs (below), but that guard is a mitigation, not a fix.

## 1. Grounded-answer eval (`server/tests/grounded_eval.py`, cases in `server/tests/grounded_cases.json`)

30 questions over the 3 sample docs. 18 answerable from text, 10 not in the docs (5 of them traps: the topic is in the docs but the specific fact isn't, e.g. casing-bolt **torque** or the occlusion **threshold**), and 2 with a photo. Every ask goes through `POST /ask`, so latency is end to end (retrieval + gate + model + formatting).

| Run | Code state | Answerable right (fact + page) | Refusals right | Hallucination | False refusals |
|---|---|---|---|---|---|
| mock (baseline) | final | 13/19\* | 7/11 | 2 (quotes a "closest" passage as if it answered) | 1 |
| nim run 1 | before fixes | 12/19 (14 after loosening 2 over-strict page keys) | 11/11 | 0 | 4 (3 gate, 1 chunk cut) |
| nim run 2 | + chunk, stemmer, refusal fixes | 17/19 | 11/11 | 0 | 1 (gate) |
| nim run 3 | + "fact first on HUD" prompt | 17/19 | 11/11 | **1: invented "60 PSI" from the photo** | 1 |
| nim run 4 | + number guard | 16/19 | 10/11 | 0 (#29 caught by guard; #22 was an honest "not documented" the parser missed) | 2 (#9: stray marker) |
| **nim run 5** | **final** | **17/19** | **11/11** | **0** (guard caught #29 again) | 1 (gate) |
| nim gate off (refusal cases only) | final | 1 photo case right | **11/11** | 0 | 0 |

\*The mock's keyword scoring is generous: some of its "correct" answers lead with the disclaimer sentence.

What the two remaining misses are, in the final run:
- **#14 "What usually causes an occlusion alarm?"** The BM25 relevance gate refuses it before the model sees anything, because "causes" isn't in the doc (it says "means… often a closed clamp"). This needs synonyms or `RETRIEVER=embed`; I haven't tested that.
- **#29 photo of an oil gauge + "Is this discharge pressure normal?"** The right range (55–70 PSI, p.14) is on the HUD, but the model claims a reading. The guard catches it: the HUD shows `⚠ Photo reading: check it yourself` and the spoken answer starts "Check this yourself: 60 is not in your docs…".

Refusals: the gate stops 8 of the 11 not-in-docs questions in about 20 ms with no model call. The model handled the 3–4 that got through (torque, flow rate, impeller, strainer schedule), and handled all 11 with the gate off.

Raw runs: `eval_results/grounded_nim_run1..5.jsonl`, `grounded_nim_gateoff.jsonl`, `grounded_mock.jsonl`.

## 2. Vision concept test (`simulator/concept_test/nim_eval.py`, same 15 cases and prompts as the SmolVLM test)

Graded C = 1, P = 0.5, W = 0. Graded file: `simulator/concept_test/nim_graded_2026-11-12.csv` (raw output in `nim_nemotron_{a,b,c}.*` and `nim_llama90b_{a,b,c}.*`).

| Model | C / P / W | Score | Read-mode | Median / p90 latency | Made up text on the unreadable plate (#6)? |
|---|---|---|---|---|---|
| **Nemotron 3.5 Super VL** | 9 / 3 / 3 | **70%** | 69% | **1.6 s / 2.5 s** | No: "Can't read that" |
| Llama 3.2 90B Vision (ends 2026-11-16) | 8 / 3 / 4 | 63% | 62% | 10.2 s / 39 s (one 75 s) | **Yes**: "Baldwin American Co." |
| SmolVLM 500M hi-res (earlier, CPU) | 2 / 6 / 7 | 33% | 44% | 11.5 s | Yes |

Nemotron's misses were the jet-engine test stand (it said "missile launcher"), the open gauge movement ("pressure switch"), and both analog gauges. On the oil gauge (#12) it gave **3 different made-up readings in 3 runs** (100/20, 200/60, 245/115), when the truth is 75 °F / 0 psi. Both models read the motor plate's RPM as 428 instead of 425. Llama's two answers for cases 11 and 12 come from the console output, because the shell timed out before its CSV was written (noted in the graded CSV).

## 3. Bugs found with the real model, and fixed

| # | Bug (how it showed up) | Fix | Test |
|---|---|---|---|
| 1 | **The model refuses in its own words inside the answer format** ("HUD: Torque not listed", "CITE: NOT_IN_DOCS"). The parser only checked line 1, so the server showed the refusal **with a source citation** as if it were an answer. | The prompt now asks for the bare marker. The parser treats NOT_IN_DOCS in HUD line 1, FULL, CITE or the whole reply as a refusal, plus a refusal-phrase regex judged on HUD line 1 and the first FULL sentence only. | 11 real Nemotron replies kept as regression cases |
| 2 | **A stray NOT_IN_DOCS threw away a correct answer** ("After 7 days of running \| NOT_IN_DOCS", CITE 1) | A stray marker in HUD line 2 is dropped and the answer is kept | same |
| 3 | **An empty or null reply read as "Not in your docs"** (`content: null` with reasoning tokens, or a cut-off `<think>`). It would have hidden every provider failure. | `message_text()` raises `LLMEmptyReply`, the HUD says `AI service error`, and the cause goes into `error`. It also handles content as a list of parts and orphan or unclosed think tags. | 4 cases |
| 4 | **No overall timeout or retry.** 90 s per request, and the 202-queue polling could run past it. The Mentra adapter gives up at **20 s**, so a slow reply showed "Can't reach answers" while the server was still waiting. | One deadline, `LLM_TIMEOUT` (default **18 s**), covering request + queue polling + **one retry on 429/5xx/network**. It fired once in testing on a NIM queue spike, as designed. | retry, no-retry-on-400, deadline |
| 5 | **Chunks cut mid-sentence** ("…Call the" \| "charge nurse…"), and the model rightly said "the passage cuts off" (a false refusal) | The model now gets each cited **page** (`LLM_PAGE_CHARS` = 1500), not the 110-word chunk. Mock path unchanged. | page-context test |
| 6 | **The stemmer missed** reassembling/reassemble and running/run, and "how long" / "usually" counted as missing topic words, so the gate refused answerable questions | The stemmer folds a final -e and doubled consonants. long / often / usually / typically are stop words. | eval #8, #10 now pass |
| 7 | **Text-only question answered "I can't read the gauge from what you showed me"** | The prompt says whether a photo is attached | prompt test |
| 8 | **The HUD led with "Can't read pressure"** and the documented 55–70 PSI only appeared in the spoken answer | The prompt says HUD line 1 carries the documented fact and line 2 asks for the reading | sampled 3/3 |
| 9 | **Invented numbers** (photo gauge readings, a photo serial cited to the manual) | **Number guard**: digits in the answer that are in neither the cited pages nor the question go into `unverified_numbers`. Their HUD line becomes a warning and the spoken answer **starts** with "Check this yourself". | 3 cases + updated older test |
| 10 | The source snippet was the chunk's first 200 characters, often the doc's disclaimer | The snippet is now the cited page's sentence that best matches the answer | snippet test |
| 11 | LLM errors and model refusals left no line, or no `image` flag, in `asks_v1.jsonl` | Every ask logs `image` and latency. Model refusals log the raw reply (400 characters), errors log the error. | harness check |

Harness-only changes: the companion real test got `--llm nim` and a 45 s browser wait in nim mode. The Mentra real harness got `LLM=nim` and waits for state instead of fixed 250 ms sleeps. Mock defaults are unchanged.

New API fields (in API.md): `refused_by: "retrieval" | "model"` and `unverified_numbers`.

## 4. What differs from mock

- The real model is **paraphrased and shorter**. The mock quotes sentences verbatim, including weak ones like the disclaimer. The real HUD reads "55-70 PSI normal / Need current pressure".
- **The mock never refuses.** Only the gate refuses for it. The real model adds a second refusal layer, and it handled the trap questions the mock "answered" with a nearby passage (impeller, strainer schedule).
- **The real model can invent numbers from photos.** The mock can't. That is why the number guard only runs on the real-model path.
- **Latency** goes from about 0 ms to about 0.6 s (median), with rare spikes of 6–8 s and one queue timeout.
- **Answers vary between runs** at temperature 0.1: the same question flipped between answer and refusal once (fixed, bug 2), and the gauge reading changed every run.

## 5. Still untested

- **The vision guard on real field photos.** Only 2 photo cases are in the grounded eval. The guard can't catch an invented reading that happens to equal a number in the docs (e.g. "reads 70 PSI" when 70 is in the manual). This needs a two-pass read or a "confirm the reading" step. *Design call, not made.*
- `RETRIEVER=embed` (it would likely fix #14; *estimate*), docs with hundreds of pages, a second real workspace, real third-party manuals.
- Concurrent users, NIM free-tier rate limits under load (not measured; no 429 was seen in about 250 calls), and cost on a paid endpoint.
- Guide mode with a real model: steps are copied from the doc by design, so the model is never asked. Unchanged.
- The photo-only (v0) path against the real model only ran inside the suites (pass/fail), not scored.
- Real glasses, a real Mentra runtime, a phone camera, the OLED's ~12-character lines.
- Llama 3.2 90B goes away 2026-11-16 03:00 CST. Its numbers here can't be re-run after that.

Reproduce: `cd server && python tests/grounded_eval.py --provider nim --out run.jsonl` (resumable; `--server URL` to reuse a running server), `python3 ../companion/tests/test_companion_real.py --llm nim`, `LLM=nim bash ../mentra_app/tests/run_harness_real.sh`, and `cd simulator/concept_test && python3 nim_eval.py --model nvidia/nemotron-3.5-super-vl-120b-a12b --ids 1,2,3` (it writes files only at the end, so run small id batches).

## G20: embeddings vs BM25 (2026-11-12, NIM, 30 cases)
| retriever | answerable correct | refusals | false refusals | hallucinations | median latency |
|---|---|---|---|---|---|
| BM25 | 17/19 | 11/11 | - | 0 | 0.64s |
| Embeddings (nvidia/nemotron-3.5-embed-1b) | 18/19 | 11/11 | 0 | 0 | 0.78s |
[FACT-measured] One extra correct answer on a tiny 19-question set: a hint, not proof. Old default nv-embedqa-e5-v5 is end-of-life (410); default changed to nemotron-3.5-embed-1b. Eval now honors RETRIEVER env. Next: bigger doc set.

## G24 (2026-11-12 21:55 CT): full path, embeddings (cosine 0.25) + NIM LLM, 40 cases
- Answerable 18/19 correct (1 caught by number guard); refusals 21/21 (11 old traps + 10 new off-topic Qs); hallucinations 0; false refusals 0.
- Refusal layer: gate 11, LLM 10. Lower cosine lets more through the gate but the LLM still says NOT_IN_DOCS, so 0.25 is safe. Latency median 1.8s, p90 5.6s.
- Run: RETRIEVER=embed EMBED_BASE_URL=... python3 tests/grounded_eval.py --provider nim (10 off-topic cases added as ids 101-110 in grounded_cases.json).
