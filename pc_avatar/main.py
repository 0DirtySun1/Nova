import os
import sys
from typing import Optional, Sequence

from PyQt5.QtCore import QObject, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import QApplication

BYE_KEYWORDS = {"bye", "bye nova"}
STOP_KEYWORDS = {"stop", "stop nova"}

if __package__ is None or __package__ == "":
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from pc_avatar.ai_brain import generate_reply, summarize_history
    from pc_avatar.avatar_gui import Avatar, LISTEN_TIMEOUT_SENTINEL
    from pc_avatar.context_store import (
        DEFAULT_CONTEXT_PATH,
        append_message,
        load_context,
        save_context,
    )
    from pc_avatar.safety_toggle import SafetyToggle
    from pc_avatar.screen_vision import get_screen_text, is_vision_ready
else:
    from .ai_brain import generate_reply, summarize_history
    from .avatar_gui import Avatar, LISTEN_TIMEOUT_SENTINEL
    from .context_store import DEFAULT_CONTEXT_PATH, append_message, load_context, save_context
    from .safety_toggle import SafetyToggle
    from .screen_vision import get_screen_text, is_vision_ready


class ReplyThread(QThread):
    """Runs the LLM + optional vision capture without blocking the UI."""

    result = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        user_text: str,
        include_screen: bool,
        history: Sequence[dict],
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._user_text = user_text
        self._include_screen = include_screen
        self._history = list(history)

    def run(self) -> None:  # type: ignore[override]
        try:
            screen_text = get_screen_text() if self._include_screen else None
            reply = generate_reply(self._user_text, screen_text, self._history)
            self.result.emit(reply)
        except Exception as exc:  # network/vision errors are reported to the UI
            self.error.emit(str(exc))


class AppController(QObject):
    """Connects the floating avatar with AI + safety toggles."""

    def __init__(self) -> None:
        super().__init__()
        self.toggle = SafetyToggle()
        self.avatar = Avatar()
        self.avatar.set_mic_enabled(self.toggle.mic_enabled)

        self._context_path = DEFAULT_CONTEXT_PATH
        self._conversation = load_context(self._context_path)
        self._auto_listen_enabled = True

        self.avatar.voice_captured.connect(self._handle_voice_text)
        self.avatar.voice_error.connect(self._handle_voice_error)
        self.avatar.response_finished.connect(self._auto_resume_listening)
        self.avatar.toggle_mic_requested.connect(self._toggle_mic)
        self.avatar.toggle_vision_requested.connect(self._toggle_vision)

        self.avatar.reset_idle()
        self.avatar.show()

        self._reply_thread: Optional[ReplyThread] = None

    # ------------------------------------------------------------------
    # Voice flow
    # ------------------------------------------------------------------
    def _handle_voice_text(self, text: str) -> None:
        if not text:
            self.avatar.reset_idle()
            return
        if self._reply_thread and self._reply_thread.isRunning():
            self.avatar.show_message("Still thinking…")
            return

        cleaned = text.strip()
        text_lower = cleaned.lower()

        if text_lower in BYE_KEYWORDS | STOP_KEYWORDS:
            self._auto_listen_enabled = False
            if text_lower in BYE_KEYWORDS:
                reply = "Bye for now! Wave me over when you want to chat again."
            else:
                reply = "Okay, I’ll stay quiet. Tap the mic button when you want me listening again."
            append_message(
                self._conversation,
                "user",
                cleaned,
                path=self._context_path,
                auto_save=False,
            )
            summary = self._summarize_and_trim(reply)
            closing_reply = reply
            if summary:
                closing_reply = f"{reply}\n\nI'll remember:\n{summary}"
            self.avatar.speak(closing_reply)
            return

        self._auto_listen_enabled = True
        self.avatar.show_message("Thinking…")
        include_screen = self.toggle.vision_enabled and is_vision_ready()
        history_for_ai = list(self._conversation)
        append_message(self._conversation, "user", cleaned, path=self._context_path)
        self._reply_thread = ReplyThread(cleaned, include_screen, history_for_ai, self)
        self._reply_thread.result.connect(self._deliver_reply)
        self._reply_thread.error.connect(self._handle_reply_error)
        self._reply_thread.finished.connect(self._clear_reply_thread)
        self._reply_thread.start()

    def _handle_voice_error(self, err: str) -> None:
        if not err or err == LISTEN_TIMEOUT_SENTINEL:
            return
        self.avatar.show_message(f"Mic error: {err}")

    # ------------------------------------------------------------------
    # Reply lifecycle
    # ------------------------------------------------------------------
    def _deliver_reply(self, reply: str) -> None:
        if reply:
            append_message(self._conversation, "assistant", reply, path=self._context_path)
            self.avatar.speak(reply)
        else:
            self.avatar.show_message("I'm speechless!")
            self._auto_resume_listening()

    def _handle_reply_error(self, err: str) -> None:
        self.avatar.show_message(f"Reply error: {err}")
        if err:
            append_message(self._conversation, "assistant", f"[error] {err}", path=self._context_path)
        self._auto_resume_listening()

    def _clear_reply_thread(self) -> None:
        self._reply_thread = None

    def _auto_resume_listening(self) -> None:
        if not self._auto_listen_enabled:
            return
        if not self.toggle.mic_enabled:
            return
        if self._reply_thread and self._reply_thread.isRunning():
            return
        # Give the UI a brief moment to settle before listening again
        QTimer.singleShot(350, self.avatar.listen)

    def _summarize_and_trim(self, assistant_reply: str) -> Optional[str]:
        append_message(
            self._conversation,
            "assistant",
            assistant_reply,
            path=self._context_path,
            auto_save=False,
        )
        summary = summarize_history(self._conversation)
        if summary:
            self._conversation = [{"role": "assistant", "content": summary}]
            save_context(self._conversation, path=self._context_path)
            return summary

        self._conversation = self._conversation[-4:]
        save_context(self._conversation, path=self._context_path)
        return None

    # ------------------------------------------------------------------
    # Safety toggles
    # ------------------------------------------------------------------
    def _toggle_mic(self) -> None:
        self.toggle.toggle_mic()
        self.avatar.set_mic_enabled(self.toggle.mic_enabled)

    def _toggle_vision(self) -> None:
        self.toggle.toggle_vision()
        status = "on" if self.toggle.vision_enabled else "off"
        if self.toggle.vision_enabled and not is_vision_ready():
            self.avatar.show_message(
                "Vision needs Tesseract OCR. Install it (see README) or pick ‘Toggle screen vision’ to disable."
            )
        else:
            self.avatar.show_message(f"Screen vision {status}")


def main() -> int:
    app = QApplication(sys.argv)
    controller = AppController()
    controller.avatar.start_roaming()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
