"""Jarvis: a home voice assistant for macOS and Windows.

Wake word ("Hey Jarvis" / "Привет, Джарвис") -> record -> Whisper -> AI brain (Claude Code, Cursor CLI or Codex)
-> voice (Edge TTS free, or ElevenLabs) -> speakers. Nothing leaves the computer until the wake word is heard.

Settings live in config.json next to this file (copy config.example.json). Run:  python jarvis.py
Extra commands:  python jarvis.py --doctor (check the setup, says what to fix)
                 python jarvis.py --test   (check microphone, voice and brain without the wake word)
                 python jarvis.py --look   (take one camera photo, prints its path)
"""
import asyncio, io, json, os, queue, re, shutil, subprocess, sys, tempfile, threading, time, wave

__version__ = "1.1.0"
HERE = os.path.dirname(os.path.abspath(__file__))
IS_WIN = sys.platform.startswith("win")
DATA = os.path.join(os.path.expanduser("~"), ".jarvis-assistant")
os.makedirs(DATA, exist_ok=True)
SR, BLOCK = 16000, 1280                                  # 80 ms frames, what openWakeWord expects

# ---------------------------------------------------------------- config
DEFAULTS = {
    "assistant_name": "Джарвис",
    "owner_name": "",                     # how Jarvis calls you, e.g. "Саша"
    "city": "",                           # for weather/time questions, e.g. "Toronto"
    "language": "ru",
    "brain": "claude",                    # claude | cursor | codex | api (Groq free key, Gemini, Ollama)
    "brain_model": "",                    # e.g. "sonnet" for claude; empty = the tool's default / auto-pick for api
    "api_url": "https://api.groq.com/openai/v1",   # brain "api": any OpenAI-compatible service
    "api_key_file": "groq_key.txt",
    "stt": "local",                       # local (faster-whisper) | server (OpenAI-compatible URL) | groq
    "stt_model": "large-v3-turbo",        # local: tiny/base/small/medium/large-v3-turbo
    "stt_url": "http://127.0.0.1:2022/v1/audio/transcriptions",
    "groq_key_file": "groq_key.txt",
    "tts": "edge",                        # edge (free) | elevenlabs
    "edge_voice": "ru-RU-DmitryNeural",
    "elevenlabs_key_file": "eleven_key.txt",
    "elevenlabs_voice": "onwK4e9ZLuTAKqWW03F9",
    "elevenlabs_model": "eleven_v4_turbo",
    "camera": True,
    "camera_index": 0,
    "mic_device": None,                   # None = system default; or a name part, e.g. "BRIO"
    "wake_threshold": 0.5,
    "followup_seconds": 10,
}

def load_config():
    cfg = dict(DEFAULTS)
    p = os.path.join(HERE, "config.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg

CFG = load_config()
NAME, OWNER = CFG["assistant_name"], CFG["owner_name"] or "друг"

def read_secret(name):
    p = os.path.join(HERE, name)
    try:
        with open(p, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""

def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)

TURNS = os.path.join(DATA, "turns.jsonl")

def log_turn(**kw):
    """One line per question: what was heard, what was answered, timings and errors. For learning from mistakes."""
    kw["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(TURNS, "a", encoding="utf-8") as f:
            f.write(json.dumps(kw, ensure_ascii=False) + "\n")
    except OSError:
        pass

# ---------------------------------------------------------------- camera
LOOK_PATH = os.path.join(DATA, "look.jpg")

def look():
    """One photo from the webcam, only when asked. Returns the jpg path."""
    import cv2
    cap = cv2.VideoCapture(int(CFG["camera_index"]), cv2.CAP_DSHOW if IS_WIN else cv2.CAP_ANY)
    try:
        frame = None
        for _ in range(15):                               # let auto-exposure settle
            ok, f = cap.read()
            if ok: frame = f
        if frame is None:
            raise RuntimeError("camera gave no picture (check camera permission)")
        cv2.imwrite(LOOK_PATH, frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return LOOK_PATH
    finally:
        cap.release()

# ---------------------------------------------------------------- speech to text
_LOCAL_STT = [None]

def wav_bytes(pcm):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())
    return buf.getvalue()

def transcribe(pcm, hint=True):
    import requests
    prompt = f"{NAME}, {OWNER}." if hint else ""
    mode = CFG["stt"]
    if mode == "local":
        if _LOCAL_STT[0] is None:
            from faster_whisper import WhisperModel
            _LOCAL_STT[0] = WhisperModel(CFG["stt_model"], device="auto", compute_type="int8")
        audio = pcm.astype("float32") / 32768.0
        segs, _ = _LOCAL_STT[0].transcribe(audio, language=CFG["language"], beam_size=1,
                                           initial_prompt=prompt or None, vad_filter=False)
        return " ".join(s.text for s in segs).strip()
    if mode == "groq":
        url, headers = "https://api.groq.com/openai/v1/audio/transcriptions", {"Authorization": "Bearer " + read_secret(CFG["groq_key_file"])}
        model = "whisper-large-v3-turbo"
    else:
        url, headers, model = CFG["stt_url"], {}, "whisper-1"
    r = requests.post(url, headers=headers, files={"file": ("a.wav", wav_bytes(pcm), "audio/wav")},
                      data={"model": model, "language": CFG["language"], "response_format": "json", "prompt": prompt}, timeout=30)
    r.raise_for_status()
    return r.json().get("text", "").strip()

# ---------------------------------------------------------------- text to speech
TAG_RE = re.compile(r"\[[A-Za-z][A-Za-z ,'-]{1,30}\]\s*")      # [warmly] etc. — ElevenLabs only
CACHE = os.path.join(DATA, "tts_cache"); os.makedirs(CACHE, exist_ok=True)

def synth(text, path):
    """Write an mp3 for text. ElevenLabs if configured, Edge TTS otherwise or as a fallback."""
    import hashlib, requests
    key = read_secret(CFG["elevenlabs_key_file"]) if CFG["tts"] == "elevenlabs" else ""
    if key:
        c = os.path.join(CACHE, "el_" + hashlib.md5((CFG["elevenlabs_voice"] + text).encode()).hexdigest() + ".mp3")
        if os.path.exists(c):
            shutil.copy(c, path); return
        try:
            r = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{CFG['elevenlabs_voice']}/stream?output_format=mp3_44100_128",
                              headers={"xi-api-key": key}, json={"text": text, "model_id": CFG["elevenlabs_model"]},
                              stream=True, timeout=(3, 15))
            r.raise_for_status()
            with open(path, "wb") as f:
                for ch in r.iter_content(8192): f.write(ch)
            if os.path.getsize(path) > 500:
                if len(text) <= 60: shutil.copy(path, c)
                return
        except Exception as e:
            log("ElevenLabs error, using Edge voice:", e)
    import edge_tts
    clean = TAG_RE.sub("", text).strip()
    async def go():
        await edge_tts.Communicate(clean, CFG["edge_voice"]).save(path)
    for attempt in range(2):
        try:
            asyncio.run(go())
            if os.path.getsize(path) > 500: return
        except Exception as e:
            log("Edge TTS error:", e)
        time.sleep(0.5)
    piper_synth(clean, path)                          # offline voice: works without internet

_PIPER = [None]
PIPER_VOICE = "ru_RU-dmitri-medium"
PIPER_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/"

def piper_synth(text, path):
    """Local Russian voice (Piper). Downloads the ~60 MB voice once, on first use."""
    import requests
    if _PIPER[0] is None:
        from piper import PiperVoice
        d = os.path.join(DATA, "piper"); os.makedirs(d, exist_ok=True)
        for ext in (".onnx", ".onnx.json"):
            f = os.path.join(d, PIPER_VOICE + ext)
            if not os.path.exists(f):
                log("скачиваю офлайн-голос Piper…")
                r = requests.get(PIPER_URL + PIPER_VOICE + ext, timeout=120); r.raise_for_status()
                with open(f, "wb") as out: out.write(r.content)
        _PIPER[0] = PiperVoice.load(os.path.join(d, PIPER_VOICE + ".onnx"))
    with wave.open(path, "wb") as w:
        _PIPER[0].synthesize_wav(text, w)

class Player:
    """Plays an mp3 through the speakers; can be stopped at once (wake word or stop command)."""
    def __init__(self):
        self.stop_flag = threading.Event()
    def play(self, path, on_frame=None):
        import miniaudio, sounddevice as sd
        dec = miniaudio.decode_file(path, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=24000)
        import numpy as np
        data = np.frombuffer(dec.samples, dtype=np.int16)
        self.stop_flag.clear()
        step = 2400                                       # 0.1 s chunks so a stop is heard immediately
        with sd.OutputStream(samplerate=24000, channels=1, dtype="int16") as out:
            for i in range(0, len(data), step):
                if self.stop_flag.is_set():
                    return False
                out.write(data[i:i + step])
                if on_frame and on_frame():
                    return False
        return True
    def stop(self):
        self.stop_flag.set()

PLAYER = Player()

# ---------------------------------------------------------------- the brain
def system_prompt():
    who = f"Твоего хозяина зовут {OWNER}, обращайся к нему по имени." if CFG["owner_name"] else ""
    city = f" Он живёт в городе {CFG['city']}." if CFG["city"] else ""
    cam = (f"\nКамера: когда просят посмотреть («посмотри», «что у меня в руке», «как я выгляжу»), выполни команду "
           f"`{sys.executable} \"{os.path.join(HERE, 'jarvis.py')}\" --look`, открой картинку {LOOK_PATH} и ответь, что видишь. "
           "Без просьбы камеру не включай.") if CFG["camera"] and CFG["brain"] != "api" else ""
    emo = ("\nГолос умеет эмоции: если в ответе есть чувство, поставь в начале одну метку на английском в квадратных скобках: "
           "[warmly], [excited], [sarcastic], [calm], [laughs]. В нейтральных ответах без меток.") if CFG["tts"] == "elevenlabs" else ""
    mem = open(MEM_FILE, encoding="utf-8").read().strip() if os.path.exists(MEM_FILE) else ""
    return (f"Ты {NAME}, домашний голосовой помощник. {who}{city} Каждый твой ответ озвучивается вслух.\n"
            "Стиль: без markdown, списков, эмодзи и ссылок. Обычно 1-2 предложения, первое короткое (до 8 слов). "
            "Если просят рассказать подробно, до 6 предложений. Спокойно, с лёгкой иронией.\n"
            "Распознавание речи иногда коверкает слова: угадывай смысл, а если совсем непонятно, коротко переспроси.\n"
            "Память: если хозяин сообщил что-то важное надолго или попросил вести себя иначе, добавь в самом конце "
            "отдельную строку «ЗАПОМНИТЬ: <коротко>». Её не озвучат.\n"
            "Файлы и настройки компьютера не трогай." + cam + emo +
            (f"\nЧто ты знаешь о хозяине:\n{mem}" if mem else ""))

MEM_FILE = os.path.join(DATA, "memory.md")

def remember(reply):
    notes = [l.split(":", 1)[1].strip() for l in reply.splitlines() if l.strip().upper().startswith("ЗАПОМНИТЬ:")]
    if notes:
        with open(MEM_FILE, "a", encoding="utf-8") as f:
            for n in notes: f.write(f"- {time.strftime('%d.%m.%Y')}: {n}\n")
        log("[запомнил]", notes)

def find_exe(name):
    p = shutil.which(name)
    if not p:
        raise RuntimeError(f"не найдена программа «{name}»: установите её и войдите в аккаунт (см. README)")
    return p

class Splitter:
    """Turns streamed text into whole sentences for the voice; hides the ЗАПОМНИТЬ line."""
    def __init__(self, out):
        self.out, self.buf, self.muted, self.full = out, "", False, ""
    def feed(self, d):
        self.full += d
        if self.muted: return
        self.buf += d
        if "ЗАПОМНИТЬ" in self.buf.upper():
            head = self.buf[:self.buf.upper().index("ЗАПОМНИТЬ")].strip()
            if head: self.out.put(head)
            self.buf, self.muted = "", True; return
        while True:
            m = re.search(r"[.!?…](\s|$)", self.buf)
            if not m or (m.end() < 12 and not self.buf[m.end():].strip()):
                break
            s, self.buf = self.buf[:m.end()].strip(), self.buf[m.end():]
            if s: self.out.put(s)
    def flush(self):
        if self.buf.strip() and not self.muted: self.out.put(self.buf.strip())
        self.buf = ""

class ClaudeBrain:
    """Claude Code: one long-lived `claude -p` process, answers stream sentence by sentence."""
    def __init__(self):
        self.p, self.t0, self.lock = None, 0.0, threading.Lock()
    def start(self):
        if self.p and self.p.poll() is None:
            self.p.kill()
        tools = ["WebSearch", "WebFetch"]
        if CFG["camera"]:
            tools += [f"Bash({sys.executable} \"{os.path.join(HERE, 'jarvis.py')}\" --look)", f"Read({LOOK_PATH})"]
        cmd = [find_exe("claude"), "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
               "--include-partial-messages", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
               "--append-system-prompt", system_prompt(), "--allowedTools", *tools]
        if CFG["brain_model"]: cmd[2:2] = ["--model", CFG["brain_model"]]
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  cwd=DATA, text=True, encoding="utf-8", bufsize=1)
        self.t0 = time.time()
    def ask(self, text, sentences):
        with self.lock:
            if self.p is None or self.p.poll() is not None or time.time() - self.t0 > 3 * 3600:
                self.start()
            sp = Splitter(sentences)
            msg = f"[Сейчас: {time.strftime('%A %d.%m.%Y %H:%M')}]\n{OWNER}: {text}"
            try:
                self.p.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": msg}}) + "\n")
                self.p.stdin.flush()
                for line in self.p.stdout:
                    try: e = json.loads(line)
                    except ValueError: continue
                    if e.get("type") == "stream_event":
                        ev = e["event"]
                        if ev.get("type") == "content_block_delta" and ev["delta"].get("type") == "text_delta":
                            sp.feed(ev["delta"]["text"])
                        elif ev.get("type") == "content_block_stop":
                            sp.flush()
                    elif e.get("type") == "result":
                        break
                else:
                    raise RuntimeError("claude exited")
            except Exception as ex:
                log("brain error:", ex); ERRORS.append(f"brain: {ex}"); self.p = None
                if not sp.full: sentences.put("Что-то сломалось, попробуй ещё раз.")
            sp.flush(); sentences.put(None)
            return sp.full

class TurnBrain:
    """Cursor CLI or Codex: one process per question, the chat is resumed by its id."""
    def __init__(self, kind):
        self.kind, self.chat, self.lock, self.first = kind, None, threading.Lock(), True
    def cmd(self, prompt):
        if self.kind == "cursor":
            c = [find_exe("cursor-agent"), "-p", "--output-format", "stream-json", "--stream-partial-output"]
            if CFG["brain_model"]: c += ["--model", CFG["brain_model"]]
            if self.chat: c += ["--resume", self.chat]
            return c + [prompt]
        c = [find_exe("codex"), "exec"]
        if self.chat: c += ["resume", self.chat]
        c += ["--json", "--skip-git-repo-check"]
        if CFG["brain_model"]: c += ["-m", CFG["brain_model"]]
        return c + [prompt]
    def ask(self, text, sentences):
        with self.lock:
            msg = f"[Сейчас: {time.strftime('%A %d.%m.%Y %H:%M')}]\n{OWNER}: {text}"
            if self.first:
                msg = system_prompt() + "\n\n" + msg
            sp = Splitter(sentences)
            try:
                p = subprocess.Popen(self.cmd(msg), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                     cwd=DATA, text=True, encoding="utf-8")
                for line in p.stdout:
                    try: e = json.loads(line)
                    except ValueError: continue
                    t = e.get("type")
                    # Cursor: {"type":"assistant","message":{"content":[{"type":"text","text":...}]}} (partial deltas)
                    if t == "assistant":
                        for part in e.get("message", {}).get("content", []):
                            if part.get("type") == "text": sp.feed(part.get("text", ""))
                    # Codex: {"type":"item.completed","item":{"type":"agent_message","text":...}}
                    elif t == "item.completed" and e.get("item", {}).get("type") == "agent_message":
                        sp.feed(e["item"].get("text", "") + " ")
                    if e.get("session_id"): self.chat = e["session_id"]
                    if e.get("chat_id"): self.chat = e["chat_id"]
                    if t == "thread.started": self.chat = e.get("thread_id")
                p.wait(timeout=180)
                self.first = False
            except Exception as ex:
                log("brain error:", ex); ERRORS.append(f"brain: {ex}")
                if not sp.full: sentences.put("Что-то сломалось, попробуй ещё раз.")
            sp.flush(); sentences.put(None)
            return sp.full

class ApiBrain:
    """Any OpenAI-compatible chat API: Groq (free key), Gemini, OpenRouter, local Ollama. Streams the answer."""
    PREFER = ("openai/gpt-oss-120b", "llama-3.3-70b-versatile", "moonshotai/kimi-k2-instruct", "qwen/qwen3-32b",
              "meta-llama/llama-4-maverick-17b-128e-instruct", "openai/gpt-oss-20b", "llama-3.1-8b-instant")
    def __init__(self):
        self.url = CFG["api_url"].rstrip("/")
        self.key = read_secret(CFG["api_key_file"])
        self.model, self.history, self.lock = CFG["brain_model"], [], threading.Lock()
    def headers(self):
        return {"Authorization": "Bearer " + self.key} if self.key else {}
    def pick_model(self):
        """Free model lists change often, so choose from what the service offers right now."""
        import requests
        r = requests.get(self.url + "/models", headers=self.headers(), timeout=10); r.raise_for_status()
        ids = [m["id"] for m in r.json().get("data", [])]
        chat = [i for i in ids if not any(x in i.lower() for x in ("whisper", "tts", "guard", "embed", "orpheus", "playai"))]
        for want in self.PREFER:
            if want in chat: return want
        if not chat: raise RuntimeError("сервис не отдал ни одной модели")
        return chat[0]
    def ask(self, text, sentences):
        import requests
        with self.lock:
            sp = Splitter(sentences)
            try:
                if not self.model:
                    self.model = self.pick_model(); log("мозг:", self.model)
                self.history.append({"role": "user", "content": f"[Сейчас: {time.strftime('%A %d.%m.%Y %H:%M')}]\n{text}"})
                self.history = self.history[-20:]                # last 10 exchanges are enough for a voice chat
                msgs = [{"role": "system", "content": system_prompt()}] + self.history
                r = requests.post(self.url + "/chat/completions", headers=self.headers(), stream=True, timeout=(5, 60),
                                  json={"model": self.model, "messages": msgs, "stream": True, "temperature": 0.6})
                if r.status_code == 429:
                    raise RuntimeError("дневной лимит бесплатного ключа исчерпан")
                r.raise_for_status()
                for raw in r.iter_lines(decode_unicode=True):
                    if not raw or not raw.startswith("data:"): continue
                    data = raw[5:].strip()
                    if data == "[DONE]": break
                    try: d = json.loads(data)["choices"][0].get("delta", {}).get("content") or ""
                    except (ValueError, KeyError, IndexError): continue
                    sp.feed(d)
                self.history.append({"role": "assistant", "content": sp.full})
            except Exception as ex:
                log("brain error:", ex); ERRORS.append(f"brain: {ex}")
                if self.history and self.history[-1]["role"] == "user": self.history.pop()
                if not sp.full:
                    sentences.put("Лимит бесплатного ключа на сегодня закончился." if "лимит" in str(ex) else "Что-то сломалось, попробуй ещё раз.")
            sp.flush(); sentences.put(None)
            return sp.full

BRAIN = {"claude": ClaudeBrain, "api": ApiBrain}.get(CFG["brain"], lambda: TurnBrain(CFG["brain"]))()

# ---------------------------------------------------------------- microphone, wake word, recording
class Mic:
    def __init__(self):
        import sounddevice as sd
        self.q = queue.Queue()
        self.stream = sd.InputStream(samplerate=SR, channels=1, dtype="int16", blocksize=BLOCK, device=CFG["mic_device"],
                                     callback=lambda d, *_: self.q.put(d[:, 0].copy()))
        self.stream.start()
    def read(self):
        return self.q.get()
    def flush(self):
        while not self.q.empty():
            try: self.q.get_nowait()
            except queue.Empty: break

NAME_RE = re.compile(r"(дж[аеэ]р[вм]и|жарвис|дарвис|джавис|j[ae]rv[iy]s)\w*[\s,.!?]*", re.I)
BYE = ("пока", "отбой", "до связи", "до свидания", "спокойной ночи", "хватит", "стоп", "замолчи", "свободен", "на сегодня всё")
FAREWELL = ("до связи", "до встречи", "до свидания", "спокойной ночи", "хорошего дня", "хорошего вечера")

class Ears:
    def __init__(self):
        from openwakeword.model import Model
        from openwakeword.vad import VAD
        import openwakeword
        try:
            openwakeword.utils.download_models(["hey_jarvis"])
        except Exception:
            pass
        self.oww = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        self.vad = VAD()
        self.floor = 100.0
    def speech(self, f):
        try: return float(self.vad.predict(f, frame_size=640))
        except Exception: return 0.0
    def wake(self, f):
        return self.oww.predict(f)["hey_jarvis"] > CFG["wake_threshold"]

def record(mic, ears, wait_start, max_len=20.0, silence_end=0.75):
    import numpy as np
    pre, frames, started, silent, t0 = [], [], False, 0.0, time.time()
    while True:
        f = mic.read(); sp = ears.speech(f)
        if not started:
            pre = (pre + [f])[-5:]
            if sp > 0.5:
                started, frames = True, pre[:]
            elif time.time() - t0 > wait_start:
                return None
            continue
        frames.append(f)
        silent = 0.0 if sp > 0.3 else silent + BLOCK / SR
        if silent > silence_end or len(frames) * BLOCK / SR > max_len:
            return np.concatenate(frames)

ERRORS = []

def speak(text_or_queue, mic, ears, stamp=None):
    """Say sentences as they arrive; synthesis of the next one overlaps playback. Wake word stops it."""
    sentences = text_or_queue
    if isinstance(text_or_queue, str):
        sentences = queue.Queue(); sentences.put(text_or_queue); sentences.put(None)
    audio, interrupted = queue.Queue(), [False]
    def worker():
        while True:
            s = sentences.get()
            if s is None:
                audio.put(None); return
            if interrupted[0]:
                continue
            path = tempfile.mktemp(suffix=".mp3", dir=DATA)
            try:
                synth(s, path); audio.put((s, path))
            except Exception as e:
                log("voice error:", e); ERRORS.append(f"voice: {e}")
    threading.Thread(target=worker, daemon=True).start()
    def heard_wake():
        while not mic.q.empty():
            if ears.wake(mic.q.get_nowait()):
                return True
        return False
    while True:
        item = audio.get()
        if item is None: break
        s, path = item
        if not interrupted[0]:
            log(f"{NAME}:", s)
            if stamp is not None: stamp.setdefault("first_voice", time.time())
            mic.flush(); ears.oww.reset()
            if not PLAYER.play(path, on_frame=heard_wake):
                interrupted[0] = True; log("[перебили]")
        try: os.remove(path)
        except OSError: pass
    return not interrupted[0]

def converse(mic, ears, first_text=None):
    """One conversation: answer, then keep listening a few seconds without the wake word."""
    text = first_text
    while True:
        if text is None:
            pcm = record(mic, ears, wait_start=7.0 if first_text is None else CFG["followup_seconds"])
            if pcm is None or len(pcm) < SR * 0.4:
                return
            t_stt = time.time(); text = transcribe(pcm); stt_s = round(time.time() - t_stt, 2)
        else:
            stt_s = None
        log(f"{OWNER}:", text)
        t = NAME_RE.sub("", text.lower()).strip(" ,.!?")
        if not t:
            text = None; first_text = ""; continue
        if len(t.split()) <= 6 and any(b in t for b in BYE):
            speak(f"До связи{', ' + CFG['owner_name'] if CFG['owner_name'] else ''}.", mic, ears); return
        sentences = queue.Queue(); result = {}; t_ask = time.time()
        th = threading.Thread(target=lambda: result.setdefault("full", BRAIN.ask(text, sentences)), daemon=True)
        th.start()
        finished = speak(sentences, mic, ears, stamp=result)
        th.join()
        full = result.get("full", "")
        log_turn(heard=text, reply=full, stt_s=stt_s, voice_after_s=round(result["first_voice"] - t_ask, 2) if "first_voice" in result else None,
                 total_s=round(time.time() - t_ask, 1), interrupted=not finished, brain=CFG["brain"], tts=CFG["tts"],
                 errors=ERRORS[:]); ERRORS.clear()
        remember(full)
        if finished and any(w in full.lower()[-80:] for w in FAREWELL):
            return
        text, first_text = None, ""

def kws_check(pcm):
    """«Слушай/Привет, Джарвис»: the English wake model misses Russian phrases, so check short bursts with Whisper."""
    try:
        t = transcribe(pcm, hint=False)
    except Exception:
        return None
    m = NAME_RE.search(t)
    if not m:
        return None
    rest = t[m.end():].strip(" .,!?")
    return rest if len(rest.split()) >= 2 else ""

def main():
    import numpy as np
    mic, ears = Mic(), Ears()
    log(f"{NAME} слушает: «Hey Jarvis» или «Привет, {NAME}». Ctrl+C — выход.")
    seg, pre, sil = [], [], 0.0
    while True:
        f = mic.read()
        woke = ears.wake(f)
        cmd = None
        sp = ears.speech(f)
        if seg:                                           # short burst of speech -> maybe «Привет, Джарвис»
            seg.append(f); sil = 0.0 if sp > 0.3 else sil + BLOCK / SR
            if len(seg) * BLOCK / SR > 6.0:
                seg = []
            elif sil > 0.45:
                pcm = np.concatenate(seg); seg = []
                if not woke and len(pcm) > 0.5 * SR:
                    cmd = kws_check(pcm)
        elif sp > 0.5:
            seg, sil = pre[:] + [f], 0.0
        pre = (pre + [f])[-4:]
        if woke or cmd is not None:
            seg = []
            try:
                if cmd:
                    converse(mic, ears, first_text=cmd)
                else:
                    speak(f"Да{', ' + CFG['owner_name'] if CFG['owner_name'] else ''}?", mic, ears)
                    converse(mic, ears)
            except Exception as e:
                log("error:", e)
            ears.oww.reset(); mic.flush()
            log("…жду имя")

def self_test():
    print("1) Голос…"); p = tempfile.mktemp(suffix=".mp3"); synth("Проверка голоса. Всё работает.", p); PLAYER.play(p); os.remove(p)
    print("2) Мозг…"); q = queue.Queue(); print("   ответ:", BRAIN.ask("Скажи одним коротким предложением, что ты на связи.", q).strip())
    if CFG["camera"]:
        print("3) Камера…"); print("   снимок:", look())
    print("4) Микрофон: скажи что-нибудь в течение 5 секунд…")
    mic, ears = Mic(), Ears(); pcm = record(mic, ears, wait_start=5)
    print("   услышал:", transcribe(pcm) if pcm is not None else "(тишина)")

def doctor():
    """Check everything a friend's computer needs, in plain words. Returns the number of problems."""
    ok = lambda m: print("  OK   ", m)
    bad = []
    def fail(m, fix):
        print("  FAIL ", m, "\n        как исправить:", fix); bad.append(m)
    print(f"Jarvis {__version__} — проверка ({sys.platform}, Python {sys.version.split()[0]})")
    if sys.version_info[:2] not in ((3, 10), (3, 11), (3, 12)):
        fail(f"Python {sys.version.split()[0]}", "нужен Python 3.10–3.12, переустанови через установщик")
    else: ok("Python")
    for mod in ("numpy", "sounddevice", "miniaudio", "openwakeword", "edge_tts", "requests"):
        try: __import__(mod); ok(f"модуль {mod}")
        except Exception as e: fail(f"модуль {mod}: {e}", "запусти установщик ещё раз")
    if os.path.exists(os.path.join(HERE, "config.json")): ok("config.json")
    else: fail("нет config.json", "скопируй config.example.json в config.json")
    if not CFG["owner_name"]: print("  INFO  owner_name не задан: Джарвис не знает, как тебя зовут")
    b = CFG["brain"]
    exe = {"claude": "claude", "cursor": "cursor-agent", "codex": "codex"}.get(b)
    if exe:
        if shutil.which(exe): ok(f"мозг {b}: найден {exe}")
        else: fail(f"мозг {b}: не найдена программа {exe}", "установи её и войди в аккаунт (README, раздел «Что нужно»)")
    elif b == "api":
        if read_secret(CFG["api_key_file"]) or "127.0.0.1" in CFG["api_url"] or "localhost" in CFG["api_url"]:
            try:
                import requests
                r = requests.get(CFG["api_url"].rstrip("/") + "/models", timeout=8,
                                 headers={"Authorization": "Bearer " + read_secret(CFG["api_key_file"])})
                ok(f"мозг api: {CFG['api_url']} отвечает") if r.ok else fail(f"мозг api: ответ {r.status_code}", "проверь ключ в " + CFG["api_key_file"])
            except Exception as e: fail(f"мозг api: {e}", "проверь интернет и api_url")
        else: fail(f"нет ключа {CFG['api_key_file']}", "получи бесплатный ключ на console.groq.com и вставь в этот файл")
    else: fail(f"неизвестный brain «{b}»", "claude, cursor, codex или api")
    if CFG["stt"] == "local":
        try: __import__("faster_whisper"); ok(f"распознавание: локально ({CFG['stt_model']})")
        except Exception as e: fail(f"faster-whisper: {e}", "запусти установщик ещё раз или поставь \"stt\": \"groq\"")
    elif CFG["stt"] == "groq" and not read_secret(CFG["groq_key_file"]):
        fail("stt groq: нет ключа " + CFG["groq_key_file"], "получи бесплатный ключ на console.groq.com")
    else: ok(f"распознавание: {CFG['stt']}")
    if CFG["tts"] == "elevenlabs" and not read_secret(CFG["elevenlabs_key_file"]):
        fail("голос ElevenLabs: нет ключа " + CFG["elevenlabs_key_file"], "вставь ключ или поставь \"tts\": \"edge\"")
    else: ok(f"голос: {CFG['tts']}")
    try:
        import sounddevice as sd
        dev = sd.query_devices(CFG["mic_device"], kind="input"); ok(f"микрофон: {dev['name']}")
        sd.query_devices(kind="output"); ok("колонки найдены")
    except Exception as e: fail(f"звук: {e}", "подключи микрофон или проверь mic_device в config.json")
    if CFG["camera"]:
        try: __import__("cv2"); ok("модуль камеры (снимок проверяется в --test)")
        except Exception as e: fail(f"камера: {e}", "запусти установщик ещё раз или поставь \"camera\": false")
    print("Итог:", "всё в порядке" if not bad else f"проблем: {len(bad)}")
    return len(bad)

def setup():
    """Interactive first-time setup, same on Windows and macOS. Writes config.json and key files."""
    import getpass, platform
    path = os.path.join(HERE, "config.json")
    cfg = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f: cfg = json.load(f)
    def ask(q, default=""):
        a = input(f"{q}{f' [{default}]' if default else ''}: ").strip()
        return a or default
    def save_key(name, label):
        k = getpass.getpass(f"Вставь {label} (ввод не виден, Enter — пропустить): ").strip()
        if k:
            fp = os.path.join(HERE, name)
            with open(fp, "w", encoding="utf-8") as f: f.write(k)
            if not IS_WIN: os.chmod(fp, 0o600)
            print("  ключ сохранён в", name)
        return bool(k) or bool(read_secret(name))
    print("\n=== Настройка Джарвиса ===")
    cfg["owner_name"] = ask("Как Джарвису тебя называть", cfg.get("owner_name", ""))
    cfg["city"] = ask("Твой город (для погоды и времени)", cfg.get("city", ""))
    found = [b for b, exe in (("claude", "claude"), ("cursor", "cursor-agent"), ("codex", "codex")) if shutil.which(exe)]
    print("\nМозг Джарвиса:")
    print("  claude — подписка Claude      " + ("(найден)" if "claude" in found else "(не установлен)"))
    print("  cursor — подписка Cursor      " + ("(найден)" if "cursor" in found else "(не установлен)"))
    print("  codex  — подписка ChatGPT     " + ("(найден)" if "codex" in found else "(не установлен)"))
    print("  api    — бесплатный ключ Groq (без подписки)")
    b = ask("Что выбрать", cfg.get("brain") or (found[0] if found else "api"))
    while b not in ("claude", "cursor", "codex", "api"):
        b = ask("Напиши claude, cursor, codex или api", "api")
    cfg["brain"] = b
    groq = False
    if b == "api":
        print("Ключ Groq: console.groq.com → API Keys → Create API Key")
        groq = save_key(cfg.get("api_key_file", "groq_key.txt"), "ключ Groq")
    strong = (sys.platform == "darwin" and platform.machine() == "arm64") or bool(shutil.which("nvidia-smi"))
    if groq or b == "api":
        cfg["stt"] = "groq"; print("Распознавание речи: Groq (тот же ключ, быстро на любом ПК)")
    elif strong:
        cfg["stt"] = "local"; cfg["stt_model"] = "large-v3-turbo"; print("Распознавание речи: на этом компьютере (мощный процессор/видеокарта)")
    else:
        print("\nКомпьютер без видеокарты NVIDIA: точное распознавание будет медленным.")
        if ask("Использовать бесплатный ключ Groq для распознавания? (y/n)", "y").lower().startswith("y"):
            cfg["stt"] = "groq"; save_key(cfg.get("groq_key_file", "groq_key.txt"), "ключ Groq (console.groq.com)")
        else:
            cfg["stt"] = "local"; cfg["stt_model"] = "small"
    if ask("\nЖивой голос ElevenLabs вместо бесплатного? Нужен их ключ (y/n)", "n").lower().startswith("y"):
        if save_key(cfg.get("elevenlabs_key_file", "eleven_key.txt"), "ключ ElevenLabs"): cfg["tts"] = "elevenlabs"
    else:
        cfg["tts"] = "edge"
    cfg["camera"] = ask("Разрешить камеру по команде «посмотри»? (y/n)", "y").lower().startswith("y")
    with open(path, "w", encoding="utf-8") as f: json.dump(cfg, f, ensure_ascii=False, indent=2)
    print("\nСохранено в config.json. Поменять можно в любой момент: запусти установщик ещё раз или открой этот файл.")

def to_logfile():
    """Background start (autostart) has no console: write everything to ~/.jarvis-assistant/jarvis.log."""
    f = open(os.path.join(DATA, "jarvis.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = f

if __name__ == "__main__":
    if sys.stdout is None or "--service" in sys.argv:
        to_logfile()
    if "--version" in sys.argv:
        print(__version__)
    elif "--setup" in sys.argv:
        setup()
    elif "--doctor" in sys.argv:
        sys.exit(1 if doctor() else 0)
    elif "--look" in sys.argv:
        print(look())
    elif "--test" in sys.argv:
        self_test()
    else:
        try:
            main()
        except KeyboardInterrupt:
            pass
