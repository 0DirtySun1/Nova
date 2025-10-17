import os
import sys
import threading
from typing import Optional, Sequence

from PyQt5.QtCore import QObject, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import QApplication

BYE_KEYWORDS = {"bye", "bye nova"}
STOP_KEYWORDS = {"stop", "stop nova"}

SESSION_CONTROL_KEYWORDS = BYE_KEYWORDS | STOP_KEYWORDS | {"goodbye", "see you", "later nova"}
SEGMENT_RULES = [
    ("session-control", SESSION_CONTROL_KEYWORDS),
    ("tasks", {"todo", "task", "remind", "schedule", "deadline", "plan"}),
    ("work", {"project", "code", "bug", "deploy", "meeting", "client"}),
    ("personal", {"family", "friend", "birthday", "relationship", "vacation", "hobby"}),
    ("wellness", {"health", "doctor", "exercise", "sleep", "diet", "meditation"}),
    ("finance", {"budget", "money", "invoice", "bill", "pay", "expense"}),
]

if __package__ is None or __package__ == "":
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from pc_avatar.ai_brain import generate_reply, summarize_history
    from pc_avatar.avatar_gui import Avatar, LISTEN_TIMEOUT_SENTINEL
    from pc_avatar.context_store import (
        DEFAULT_CONTEXT_PATH,
        DEFAULT_SEGMENT,
        append_message,
        load_context,
        save_context,
        update_segment_summary,
    )
    from pc_avatar.logs import rebuild_obsidian_vault
    from pc_avatar.safety_toggle import SafetyToggle
    from pc_avatar.screen_vision import get_screen_text, is_vision_ready
else:
    from .ai_brain import generate_reply, summarize_history
    from .avatar_gui import Avatar, LISTEN_TIMEOUT_SENTINEL
    from .context_store import (
        DEFAULT_CONTEXT_PATH,
        DEFAULT_SEGMENT,
        append_message,
        load_context,
        save_context,
        update_segment_summary,
    )
    from .logs import rebuild_obsidian_vault
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
        memory_summary: Optional[str] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._user_text = user_text
        self._include_screen = include_screen
        self._history = list(history)
        self._memory_summary = memory_summary

    def run(self) -> None:  # type: ignore[override]
        try:
            screen_text = get_screen_text() if self._include_screen else None
            reply = generate_reply(
                self._user_text,
                screen_text,
                self._history,
                memory_summary=self._memory_summary,
            )
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
        self._last_segment = DEFAULT_SEGMENT
        self._obsidian_thread = None
        self._context_summary: Optional[str] = None
        if self._conversation and len(self._conversation) == 1:
            first_entry = self._conversation[0]
            content = first_entry.get("content") if isinstance(first_entry, dict) else None
            if isinstance(content, str) and content.count("\n") >= 1:
                self._context_summary = content

        self.avatar.voice_captured.connect(self._handle_voice_text)
        self.avatar.voice_error.connect(self._handle_voice_error)
        self.avatar.response_finished.connect(self._auto_resume_listening)
        self.avatar.toggle_mic_requested.connect(self._toggle_mic)
        self.avatar.toggle_vision_requested.connect(self._toggle_vision)
        self.avatar.shutdown_requested.connect(self._handle_shutdown_request)

        self.avatar.reset_idle()
        self.avatar.show()

        self._reply_thread = None
        self._shutdown_in_progress = False

    # ------------------------------------------------------------------
    # Voice flow
    # ------------------------------------------------------------------
    def _infer_segment(self, role: str, content: str, *, include_screen: bool = False) -> str:
        text = content.lower()
        for segment, keywords in SEGMENT_RULES:
            if any(keyword in text for keyword in keywords):
                return segment
        if include_screen and role == "user":
            return "screen-context"
        return DEFAULT_SEGMENT

    def _handle_voice_text(self, text: str) -> None:
        if not text:
            self.avatar.reset_idle()
            return
        if self._reply_thread and self._reply_thread.isRunning():
            self.avatar.show_message("Still thinking…")
            return

        cleaned = text.strip()
        if not cleaned:
            self.avatar.reset_idle()
            return

        text_lower = cleaned.lower()
        last_word = ""
        if cleaned:
            parts = cleaned.split()
            if parts:
                candidate = parts[-1].rstrip(".,!?;:\"'")
                last_word = candidate.lower()

        command_type: Optional[str] = None
        if text_lower in BYE_KEYWORDS:
            command_type = "bye"
        elif text_lower in STOP_KEYWORDS:
            command_type = "stop"
        elif last_word in {"bye", "stop"}:
            command_type = last_word

        if command_type is not None:
            self._auto_listen_enabled = False
            if command_type == "bye":
                reply = "Bye for now! Wave me over when you want to chat again."
            else:
                reply = "Okay, I'll stay quiet. Tap the mic button when you want me listening again."
            segment = self._infer_segment("user", cleaned)
            self._last_segment = segment
            append_message(
                self._conversation,
                "user",
                cleaned,
                path=self._context_path,
                auto_save=False,
                segment=segment,
            )
            self._summarize_and_trim(reply)
            self.avatar.speak(reply)
            self._trigger_obsidian_export()
            return

        self._auto_listen_enabled = True
        self.avatar.show_message("Thinking…")
        include_screen = self.toggle.vision_enabled and is_vision_ready()
        segment = self._infer_segment("user", cleaned, include_screen=include_screen)
        self._last_segment = segment
        history_for_ai = list(self._conversation)
        append_message(
            self._conversation,
            "user",
            cleaned,
            path=self._context_path,
            segment=segment,
            metadata={"include_screen": include_screen},
        )
        self._reply_thread = ReplyThread(
            cleaned,
            include_screen,
            history_for_ai,
            memory_summary=self._context_summary,
            parent=self,
        )
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
            segment_guess = self._infer_segment("assistant", reply)
            segment = segment_guess if segment_guess != DEFAULT_SEGMENT else self._last_segment
            self._last_segment = segment or DEFAULT_SEGMENT
            append_message(
                self._conversation,
                "assistant",
                reply,
                path=self._context_path,
                segment=segment,
            )
            self.avatar.speak(reply)
        else:
            self.avatar.show_message("I'm speechless!")
            self._auto_resume_listening()

    def _handle_reply_error(self, err: str) -> None:
        self.avatar.show_message(f"Reply error: {err}")
        if err:
            append_message(
                self._conversation,
                "assistant",
                f"[error] {err}",
                path=self._context_path,
                segment="system",
            )
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
            segment=self._last_segment,
        )
        summary = summarize_history(self._conversation)
        if summary:
            self._conversation = [{"role": "assistant", "content": summary}]
            update_segment_summary(self._last_segment or DEFAULT_SEGMENT, summary)
            save_context(self._conversation, path=self._context_path)
            self._context_summary = summary
            return summary

        self._conversation = self._conversation[-4:]
        save_context(self._conversation, path=self._context_path)
        return None

    def _trigger_obsidian_export(self) -> None:
        self._trigger_obsidian_export_internal(blocking=False)

    def _trigger_obsidian_export_internal(self, *, blocking: bool) -> None:
        if blocking:
            if self._obsidian_thread and self._obsidian_thread.is_alive():
                self._obsidian_thread.join(timeout=5)
            self._obsidian_thread = None
            try:
                rebuild_obsidian_vault()
            except Exception:
                pass
            return

        if self._obsidian_thread and self._obsidian_thread.is_alive():
            return

        def _run_export() -> None:
            try:
                rebuild_obsidian_vault()
            except Exception:
                pass
            finally:
                self._obsidian_thread = None

        self._obsidian_thread = threading.Thread(
            target=_run_export,
            name="ObsidianExport",
            daemon=True,
        )
        self._obsidian_thread.start()

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

    def _finalize_conversation(self) -> None:
        try:
            summary = summarize_history(self._conversation)
        except Exception:
            summary = None
        if summary:
            try:
                update_segment_summary(self._last_segment or DEFAULT_SEGMENT, summary)
            except Exception:
                pass
            self._conversation = [{"role": "assistant", "content": summary}]
            self._context_summary = summary
        try:
            save_context(self._conversation, path=self._context_path)
        except Exception:
            pass

    def _handle_shutdown_request(self) -> None:
        if self._shutdown_in_progress:
            return
        self._shutdown_in_progress = True
        self._auto_listen_enabled = False
        if self._reply_thread and self._reply_thread.isRunning():
            self._reply_thread.wait(2000)
        self.avatar.speak("Saving notes. One moment…")
        self._finalize_conversation()
        self._trigger_obsidian_export_internal(blocking=True)
        QApplication.quit()


def main() -> int:
    app = QApplication(sys.argv)
    controller = AppController()
    controller.avatar.start_roaming()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
