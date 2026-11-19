"""Offline text-to-speech and (optional) speech-to-text.

TTS order: Piper (natural voice, needs `pip install piper-tts` + a voice .onnx in voices/)
           -> espeak-ng (robotic but tiny; apt/brew install espeak-ng)
           -> none (the glasses just show text).
Output is always 16 kHz, 16-bit, mono WAV with a plain 44-byte header (what the firmware expects).

STT: faster-whisper "tiny.en" if installed (`pip install faster-whisper`), else voice questions are ignored.
"""
import glob, io, os, shutil, subprocess, tempfile, wave

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_RATE = 16000


def _resample_to_16k_mono(wav_bytes: bytes) -> bytes:
    with wave.open(io.BytesIO(wav_bytes)) as w:
        ch, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.readframes(w.getnframes())
    import numpy as np
    a = np.frombuffer(frames, dtype={1: np.uint8, 2: np.int16, 4: np.int32}[width]).astype(np.float32)
    if width == 1: a = (a - 128) * 256
    if width == 4: a = a / 65536
    if ch > 1: a = a.reshape(-1, ch).mean(axis=1)
    if rate != OUT_RATE and len(a):
        n = int(len(a) * OUT_RATE / rate)
        a = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a)
    peak = float(np.max(np.abs(a))) if len(a) else 0
    if peak > 0: a = a * (0.9 * 32767 / peak)          # normalise loudness for the small transducer
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(OUT_RATE)
        w.writeframes(a.clip(-32768, 32767).astype(np.int16).tobytes())
    return buf.getvalue()


class TTS:
    def __init__(self):
        self.engine, self._piper = "none", None
        want = os.environ.get("TTS_ENGINE", "auto").lower()
        voice = os.environ.get("PIPER_VOICE") or next(iter(sorted(glob.glob(os.path.join(HERE, "voices", "*.onnx")))), None)
        if want in ("auto", "piper") and voice:
            try:
                from piper import PiperVoice
                self._piper = PiperVoice.load(voice)
                self.engine = "piper:" + os.path.basename(voice)
            except Exception as e:  # noqa: BLE001
                print(f"[tts] piper unavailable ({e}); trying espeak-ng")
        if self.engine == "none" and want in ("auto", "espeak", "piper"):
            self._espeak = shutil.which("espeak-ng") or shutil.which("espeak")
            if self._espeak: self.engine = "espeak-ng"

    def synth(self, text: str) -> bytes | None:
        text = (text or "").strip()
        if not text or self.engine == "none":
            return None
        if self._piper:
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                self._piper.synthesize_wav(text, w)
            return _resample_to_16k_mono(buf.getvalue())
        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            subprocess.run([self._espeak, "-s", "165", "-w", f.name, text], check=True, capture_output=True, timeout=30)
            return _resample_to_16k_mono(open(f.name, "rb").read())


class STT:
    def __init__(self):
        self.engine, self._m = "none", None
        if os.environ.get("STT_ENGINE", "auto").lower() == "none":
            return
        try:
            from faster_whisper import WhisperModel
            self._m = WhisperModel(os.environ.get("WHISPER_MODEL", "tiny.en"), device="cpu", compute_type="int8")
            self.engine = "faster-whisper:" + os.environ.get("WHISPER_MODEL", "tiny.en")
        except Exception as e:  # noqa: BLE001
            print(f"[stt] voice questions disabled ({type(e).__name__}: {e})")

    def transcribe(self, wav_bytes: bytes) -> str:
        """WAV bytes -> text. Decodes the WAV ourselves (no ffmpeg/PyAV needed). Never raises."""
        if not self._m:
            return ""
        try:
            import numpy as np
            pcm16 = _resample_to_16k_mono(wav_bytes)
            with wave.open(io.BytesIO(pcm16)) as w:
                a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
            segs, _ = self._m.transcribe(a, beam_size=1, language="en", vad_filter=True)
            return " ".join(s.text.strip() for s in segs).strip()
        except Exception as e:  # noqa: BLE001
            print(f"[stt] failed: {type(e).__name__}: {e}")
            return ""
