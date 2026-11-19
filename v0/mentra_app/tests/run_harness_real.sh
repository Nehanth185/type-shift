#!/usr/bin/env bash
# Run tests/harness_real.ts against the REAL server: mock LLM, fresh temp DB, sample workspace "demo",
# AUTH_MODE=worker with tokens for w1 and w2. No network, no API key, no Mentra account.
#   bash tests/run_harness_real.sh
#   LLM=nim bash tests/run_harness_real.sh   # real model on NVIDIA NIM (needs NVIDIA_API_KEY, ~5 calls)
set -eu
cd "$(dirname "$0")/.."
APPDIR=$(pwd); SERVER=$APPDIR/../server
TMP=$(mktemp -d)
LLM=${LLM:-mock}
export LLM_PROVIDER=$LLM HARNESS_LLM=$LLM TTS_ENGINE=none STT_ENGINE=none RETRIEVER=bm25 SEED_DEMO=1 AUTH_MODE=worker DEVICE_TOKEN=
export GLASSES_DB=$TMP/g.db LOG_DIR=$TMP/logs NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost
[ "$LLM" = mock ] && { unset NVIDIA_API_KEY LLM_API_KEY 2>/dev/null || true; }
PORT=$(python3 -c "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1])")
TOKEN_W1=$(cd "$SERVER" && python3 workers.py add w1 2>/dev/null)
TOKEN_W2=$(cd "$SERVER" && python3 workers.py add w2 2>/dev/null)
(cd "$SERVER" && exec python3 -m uvicorn server:app --port "$PORT" --log-level warning) > "$TMP/server.log" 2>&1 &
SP=$!
trap 'kill $SP 2>/dev/null; rm -rf "$TMP"' EXIT
for i in $(seq 1 100); do curl -s --noproxy '*' "http://127.0.0.1:$PORT/health" >/dev/null && break; sleep 0.1; done
REAL_URL=http://127.0.0.1:$PORT TOKEN_W1=$TOKEN_W1 TOKEN_W2=$TOKEN_W2 LOG_FILE=$TMP/logs/asks_v1.jsonl \
  node --experimental-strip-types tests/harness_real.ts || { echo "--- server log"; tail -30 "$TMP/server.log"; exit 1; }
