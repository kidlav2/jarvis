# Instructions for AI agents

This file is for AI coding agents (Claude Code, Cursor, Codex, Copilot…) asked to **install, configure, troubleshoot or change** Jarvis for a user. Talk to the user in their language (usually Russian).

## What Jarvis is

One Python file, `jarvis.py`, that runs a voice loop:
wake word (openWakeWord `hey_jarvis` + a Whisper check for «Привет/Слушай, Джарвис») → record (Silero VAD) → speech-to-text → brain → text-to-speech → speakers.

| Part | Options (`config.json`) | Code |
|---|---|---|
| Brain | `claude` (persistent `claude -p` stream-json), `cursor` / `codex` (one CLI call per turn, resumed by chat id), `api` (OpenAI-compatible HTTP: Groq, Gemini, Ollama) | `ClaudeBrain`, `TurnBrain`, `ApiBrain` |
| Speech-to-text | `local` (faster-whisper), `groq`, `server` (any OpenAI-compatible URL) | `transcribe()` |
| Voice | `edge` (free), `elevenlabs`; Piper offline fallback | `synth()`, `piper_synth()` |
| Camera | OpenCV, one photo on request (`--look`) | `look()` |

User data lives in `~/.jarvis-assistant/`: `memory.md`, `turns.jsonl` (one JSON line per question: heard, reply, timings, errors), `jarvis.log`.

## Installing for a user

1. Use the installer, do not hand-roll a setup: macOS `bash install.sh`, Windows `install.cmd`. Both are idempotent.
2. The installer runs `jarvis.py --setup` (interactive) — let the user answer. If you must configure non-interactively, write `config.json` from `config.example.json`; every key is documented in `DEFAULTS` at the top of `jarvis.py`.
3. Run `python jarvis.py --doctor` (inside `.venv`). It exits non-zero and prints a fix for every problem. Fix them in order.
4. Run `python jarvis.py --test` for a real check of voice, brain, camera and microphone.

Python must be 3.10–3.12 (onnxruntime / openWakeWord wheels). Never use a newer Python.

## Rules

- **Keys are secrets.** They live only in `*_key.txt` next to `jarvis.py` (git-ignored). Never print them, never put them in `config.json`, logs, commits or chat. Ask the user to paste a key into the file themselves, or use `--setup`, which reads it hidden.
- **Do not change OS privacy settings** (microphone, camera). Tell the user what to allow and where.
- **Privacy:** audio never leaves the machine before the wake word. Keep it that way: no always-on cloud recognition, no camera without an explicit request.
- Russian users: keep user-facing strings in Russian, plain and short.

## Changing the code

- Keep it one file with no new heavy dependencies unless the user asks; add any new package to `requirements.txt`.
- New config keys go into `DEFAULTS` with a comment, into `config.example.json` if users should see them, and into the README table.
- Check before finishing: `python -m py_compile jarvis.py`, `python jarvis.py --doctor`, and the part you changed with `--test`.
- **Versions:** bump `__version__` in `jarvis.py` (SemVer), add an entry at the top of `CHANGELOG.md`, commit, then tag `vX.Y.Z` and create a GitHub release with the changelog entry as notes.

## Troubleshooting map

| Symptom | Look at | Usual fix |
|---|---|---|
| Never wakes up | `jarvis.log`, mic in `--doctor` | set `mic_device`; say «Hey Jarvis» clearly; lower `wake_threshold` to 0.4 |
| Slow answers | `turns.jsonl` → `stt_s`, `voice_after_s` | `stt: groq` or `stt_model: small` on weak PCs |
| Silent replies | `errors` in `turns.jsonl` | Edge TTS outages happen: Piper fallback should speak; check ElevenLabs key/quota |
| Brain errors | `jarvis.log` "brain error" | CLI not logged in (`claude`, `cursor-agent login`, `codex login`) or Groq daily limit |
| Camera fails | `--look` output | macOS: allow Camera for Terminal/Python; Windows: try `camera_index` 1 |
