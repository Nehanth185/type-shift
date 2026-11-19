#!/usr/bin/env bash
# End-to-end test of the server with no API key and no hardware.
#   bash tests/run_tests.sh
# Starts the server in mock mode, runs the fake device against it (photo, typed question,
# spoken question, inline audio, dark photo, raw-JPEG body, bad input), then repeats one ask
# through the REAL OpenAI-compatible provider code pointed at a local fake endpoint.
set -u
cd "$(dirname "$0")/.."
IMG_DIR=../../../simulator/test_images
IMG=$IMG_DIR/pressure_gauge.jpg; [ -f "$IMG" ] || IMG=$(ls $IMG_DIR/*.jpg | head -1)
TMP=$(mktemp -d); PASS=0; FAIL=0
ok()   { echo "PASS  $1"; PASS=$((PASS+1)); }
bad()  { echo "FAIL  $1"; FAIL=$((FAIL+1)); }
wait_up() { for i in $(seq 1 60); do curl -s --noproxy '*' "$1/health" >/dev/null && return 0; sleep 0.5; done; return 1; }

python3 -c "from PIL import Image; Image.new('RGB',(640,480),(8,8,8)).save('$TMP/dark.jpg')"
if command -v espeak-ng >/dev/null; then espeak-ng -w "$TMP/q.wav" "What is the pressure on this gauge?"; fi

LLM_PROVIDER=mock PORT=8765 LOG_DIR="$TMP/logs" python3 server.py > "$TMP/server.log" 2>&1 & SP=$!
trap 'kill $SP ${FP:-} ${RP:-} 2>/dev/null; rm -rf "$TMP"' EXIT
wait_up http://127.0.0.1:8765 && ok "server starts (mock)" || { bad "server start"; cat "$TMP/server.log"; exit 1; }
S=http://127.0.0.1:8765
curl -s --noproxy '*' $S/health | tee "$TMP/h.json"; echo

python3 fake_device.py "$IMG" --server $S --out "$TMP/a.wav" > "$TMP/1.txt" && grep -q '"hud_text"' "$TMP/1.txt" && [ -s "$TMP/a.wav" ] \
  && ok "photo -> hud_text + audio_url WAV" || bad "photo ask"
python3 -c "import wave;w=wave.open('$TMP/a.wav');assert (w.getframerate(),w.getnchannels(),w.getsampwidth())==(16000,1,2)" \
  && ok "audio is 16 kHz mono 16-bit (firmware format)" || bad "audio format"
python3 fake_device.py "$IMG" --server $S -q "What does the gauge read?" --out "$TMP/t.wav" > "$TMP/2.txt" && grep -q 'What does the gauge read' "$TMP/2.txt" \
  && ok "typed question passed through" || bad "typed question"
python3 fake_device.py "$IMG" --server $S --pcm --out "$TMP/p.wav" > "$TMP/3.txt" && [ -s "$TMP/p.wav" ] \
  && ok "inline pcm_b64 audio mode" || bad "pcm mode"
python3 fake_device.py "$TMP/dark.jpg" --server $S --out "$TMP/d.wav" > "$TMP/4.txt" && grep -q "Can't read that" "$TMP/4.txt" \
  && ok "dark photo -> \"Can't read that\"" || bad "dark photo"
if [ -f "$TMP/q.wav" ]; then
  python3 fake_device.py "$IMG" --server $S --voice "$TMP/q.wav" --out "$TMP/v.wav" > "$TMP/5.txt"
  if grep -q '"heard": ""' "$TMP/5.txt"; then echo "SKIP  spoken question (no STT engine installed)";
  else grep -i -q 'pressure' "$TMP/5.txt" && ok "spoken question transcribed: $(grep '"heard"' "$TMP/5.txt" | head -1 | xargs)" || bad "spoken question"; fi
fi
curl -s --noproxy '*' -X POST -H 'Content-Type: image/jpeg' --data-binary @"$IMG" "$S/ask?audio=none" | grep -q hud_text \
  && ok "raw image/jpeg body" || bad "raw body"
[ "$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' -X POST -F image=@"$TMP/h.json" $S/ask)" = 400 ] \
  && ok "non-JPEG rejected with 400" || bad "bad input"
[ "$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' $S/last)" = 200 ] && ok "/last returns last photo" || bad "/last"

# ---- real provider code path against a fake OpenAI-compatible endpoint
python3 tests/fake_llm_endpoint.py 8799 & FP=$!
LLM_PROVIDER=openai LLM_API_KEY=dummy LLM_BASE_URL=http://127.0.0.1:8799/v1 LLM_MODEL=meta/llama-3.2-90b-vision-instruct \
  PORT=8766 LOG_DIR="$TMP/logs2" no_proxy='*' NO_PROXY='*' python3 server.py > "$TMP/server2.log" 2>&1 & RP=$!
wait_up http://127.0.0.1:8766 || { bad "server start (openai provider)"; cat "$TMP/server2.log"; }
python3 fake_device.py "$IMG" --server http://127.0.0.1:8766 --out "$TMP/o.wav" > "$TMP/6.txt"
grep -q "Fake LLM: request OK" "$TMP/6.txt" && ok "openai provider: NIM-shaped request (no system role, image <180 KB b64), reply parsed" \
  || { bad "openai provider path"; cat "$TMP/6.txt"; }

echo; echo "$PASS passed, $FAIL failed"; [ $FAIL -eq 0 ]
