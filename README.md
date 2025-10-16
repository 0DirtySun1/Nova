A floating desktop companion that listens, talks with natural speech, and roams freely across your screen. The avatar stays responsive by running speech recognition, text-to-speech, and AI calls on background threads. Use it as the foundation for richer app-aware interactions.

# Project Nova PC Avatar
## Quick start

1. Create/activate a Python 3.11 environment.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Launch the avatar:
   ```bash
   python pc_avatar/main.py
   ```

### Provide your OpenAI API key (and project)

Nova auto-detects your credentials from the environment or from a local `.env` file.

- **Environment variables** (recommended):
   ```bat
   setx OPENAI_API_KEY "sk-proj-..."
   setx OPENAI_PROJECT "proj_..."
   ```
   Restart VS Code/terminals afterward so the values are picked up.
- **.env file**: create `pc_avatar/.env` (or a project-level `.env`) containing:
   ```ini
   OPENAI_API_KEY=sk-proj-...
   OPENAI_PROJECT=proj_...
   ```
   `.env` values override any system-wide settings, so it’s a handy way to keep per-project keys without touching global environment variables.

Where do these come from?

1. Visit <https://platform.openai.com/api-keys> and click **Create secret key**. Use the **You** tab so you receive a personal project-scoped key (`sk-proj-...`). Copy it once; the portal won’t show it again.
2. Grab the corresponding project ID (`proj_...`) from the key dialog or from **Dashboard → Settings → Project info**.

If you see “OpenAI rejected the API key,” regenerate a fresh key and double-check both values above are set. When the API complains about a missing project, set `OPENAI_PROJECT` or `OPENAI_PROJECT_ID` as shown.

Nova also keeps a running conversation history in `pc_avatar/context.json` so future replies stay in character and remember what you’ve discussed. Delete that file any time you want a clean slate.

   ### Speech synthesis reliability

   Google TTS occasionally rejects requests even though it returns HTTP 200. Nova now falls back to the local Windows speech engine via `pyttsx3` when that happens. Make sure the dependency is installed:

   ```bash
   pip install pyttsx3
   ```

### Optional: enable screen vision (OCR)

Screen capture uses Tesseract OCR. Install it to let Nova read the screen:

1. Download the Windows installer from <https://github.com/UB-Mannheim/tesseract/wiki> (choose the latest 64-bit build).
2. Run the installer and keep the default path (`C:\Program Files\Tesseract-OCR`).
3. Add that folder to your `PATH`, or set the environment variable `TESSERACT_PATH` to the full `tesseract.exe` location.
4. Restart Nova. If you skip these steps, Nova simply skips screen vision and you can disable it via the context menu.
5. Nova now captures **all** connected monitors. If you only want a single display, temporarily unplug/disable the extras or edit `screen_vision.py` to pass a region instead.

## Interacting with Nova

- **Talk**: Click the “🎙️ Talk to me” button or right-click → *Enable microphone* to allow listening.
- **Pause the auto-listen loop**: Say “stop,” “stop nova,” “bye,” or “bye nova” to pause the continuous listening cycle. Tap the talk button to resume.
- **Long thoughts welcome**: Nova keeps recording until it hears ~2 seconds of silence, so you can speak naturally without being cut off mid-sentence.
- **Devices**: Tap the ⚙️ gear next to the talk button to pick microphone and speaker devices without leaving the desktop.
- **Move**: Drag the anime-style companion anywhere or let it wander—Nova walks using a looping humanoid animation.
- **Menu**: Right-click the character for quick toggles (microphone, screen vision, audio settings) or to quit.
- **Replies**: Nova fetches screen text (when vision is enabled) and streams its response with non-blocking speech.
   - When you say one of the pause keywords, Nova writes a short AI-generated summary to `context.json` so the next chat starts from the essentials.

## Architecture notes

- `avatar_gui.py` renders the frameless floating widget, animates movement, and exposes signals for voice/text events.
- `main.py` wires the avatar to `ai_brain.generate_reply`, `screen_vision.get_screen_text`, and `SafetyToggle` controls.
- Speech recognition (Google) and gTTS run inside `QThread`s; audio playback uses `QMediaPlayer` to avoid UI freezes.

## Next steps

- Add app-specific actions by hooking into `AppController` once Nova’s replies should trigger workflows.
- Swap in custom sprite sheets by adjusting `_build_character_frames` in `avatar_gui.py` (or load GIFs from `pc_avatar/assets/`).
- Extend the context menu with quick shortcuts for future productivity integrations.
