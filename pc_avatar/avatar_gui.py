import os
import random
import tempfile
from typing import Dict, List, Optional, Tuple

import speech_recognition as sr
from gtts import gTTS
try:
    import pyttsx3
except Exception:  # pragma: no cover - optional dependency or platform issue
    pyttsx3 = None  # type: ignore
from PyQt5.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QThread,
    QTimer,
    QUrl,
    Qt,
    pyqtSignal,
)
from PyQt5.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt5.QtMultimedia import (
    QAudioOutputSelectorControl,
    QMediaContent,
    QMediaPlayer,
)
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)


AUDIO_OUTPUT_CONTROL_IID = "org.qt-project.qt.audiooutputselectorcontrol/5.0"
LISTEN_TIMEOUT_SENTINEL = "__LISTEN_TIMEOUT__"


class VoiceThread(QThread):
    """Background voice listener so the GUI thread stays responsive."""

    result = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        device_index: Optional[int],
    timeout: float = 8.0,
    phrase_time_limit: Optional[float] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._device_index = device_index
        self._timeout = timeout
        self._phrase_time_limit = phrase_time_limit

    def run(self) -> None:  # type: ignore[override]
        recognizer = sr.Recognizer()
        recognizer.dynamic_energy_threshold = True
        recognizer.pause_threshold = 2.0
        recognizer.non_speaking_duration = 0.7
        try:
            with sr.Microphone(device_index=self._device_index) as source:
                try:
                    recognizer.adjust_for_ambient_noise(source, duration=0.5)
                    audio = recognizer.listen(
                        source,
                        timeout=self._timeout,
                        phrase_time_limit=self._phrase_time_limit,
                    )
                    text = recognizer.recognize_google(audio, language="en-US")
                    self.result.emit(text)
                except sr.WaitTimeoutError:
                    self.error.emit(LISTEN_TIMEOUT_SENTINEL)
                except Exception as exc:  # pragma: no cover - speech errors at runtime
                    self.error.emit(str(exc))
        except OSError as exc:
            self.error.emit(str(exc))


class TTSThread(QThread):
    """Generate TTS audio without blocking the UI thread."""

    done = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._text = text

    def run(self) -> None:  # type: ignore[override]
        gtts_error: Optional[Exception] = None
        try:
            path = self._generate_with_gtts(self._text)
            self.done.emit(path)
            return
        except Exception as exc:  # pragma: no cover - network / IO errors
            gtts_error = exc

        try:
            path = self._generate_with_pyttsx3(self._text)
            self.done.emit(path)
        except Exception as fallback_exc:  # pragma: no cover - platform-specific errors
            parts = []
            if gtts_error is not None:
                parts.append(f"gTTS failed: {gtts_error}")
            parts.append(f"Offline TTS failed: {fallback_exc}")
            self.error.emit("; ".join(parts))

    @staticmethod
    def _generate_with_gtts(text: str) -> str:
        fd, tmp_path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        tts = gTTS(text)
        tts.save(tmp_path)
        return tmp_path

    @staticmethod
    def _generate_with_pyttsx3(text: str) -> str:
        if pyttsx3 is None:
            raise RuntimeError("pyttsx3 is not installed; run `pip install pyttsx3`")
        fd, tmp_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        engine = pyttsx3.init()
        engine.save_to_file(text, tmp_path)
        engine.runAndWait()
        engine.stop()
        return tmp_path


class DeviceSettingsDialog(QDialog):
    """Simple dialog to pick microphone and playback devices."""

    def __init__(
        self,
        microphone_names: List[str],
        current_mic_index: Optional[int],
        audio_outputs: List[Tuple[str, str]],
        current_output_id: Optional[str],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Audio Settings")
        self.setModal(True)
        self._selected_mic: Optional[int] = current_mic_index
        self._selected_output: Optional[str] = current_output_id

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        # Microphone selection
        mic_label = QLabel("Input device (microphone)", self)
        layout.addWidget(mic_label)

        self.mic_combo = QComboBox(self)
        self.mic_combo.setMinimumWidth(260)
        self.mic_combo.addItem("System default", userData=None)
        for index, name in enumerate(microphone_names):
            self.mic_combo.addItem(f"{index}: {name}", userData=index)
        if current_mic_index is None:
            self.mic_combo.setCurrentIndex(0)
        else:
            for row in range(self.mic_combo.count()):
                if self.mic_combo.itemData(row) == current_mic_index:
                    self.mic_combo.setCurrentIndex(row)
                    break
        layout.addWidget(self.mic_combo)

        # Output selection
        output_label = QLabel("Output device (speaker)", self)
        layout.addWidget(output_label)

        self.output_combo = QComboBox(self)
        self.output_combo.setMinimumWidth(260)
        if audio_outputs:
            for device_id, description in audio_outputs:
                self.output_combo.addItem(description, userData=device_id)
            if current_output_id is not None:
                for row in range(self.output_combo.count()):
                    if self.output_combo.itemData(row) == current_output_id:
                        self.output_combo.setCurrentIndex(row)
                        break
        else:
            self.output_combo.addItem("System default", userData=None)
        layout.addWidget(self.output_combo)

        # Dialog buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:  # type: ignore[override]
        self._selected_mic = self.mic_combo.currentData()
        self._selected_output = self.output_combo.currentData()
        super().accept()

    def selected_mic_index(self) -> Optional[int]:
        return self._selected_mic

    def selected_output_id(self) -> Optional[str]:
        return self._selected_output


class Avatar(QWidget):
    """Floating avatar companion with animated walk cycle and audio controls."""

    voice_captured = pyqtSignal(str)
    voice_error = pyqtSignal(str)
    response_finished = pyqtSignal()
    toggle_vision_requested = pyqtSignal()
    toggle_mic_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._mic_enabled = True
        self._mic_device_index: Optional[int] = None
        self._audio_output_id: Optional[str] = None
        self._dragging = False
        self._drag_offset = QPoint()
        self._base_idle_text = "Hey, I'm Nova 👋"
        self._current_tmp_path: Optional[str] = None
        self._speech_text = ""
        self._listening_thread: Optional[VoiceThread] = None
        self._tts_thread: Optional[TTSThread] = None
        self._pose = "idle"
        self._frame_index = 0

        self._setup_window()
        self._setup_ui()
        self._setup_animation()
        self._setup_motion()
        self._setup_audio()

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------
    def speak(self, text: str) -> None:
        if not text:
            return
        self._speech_text = text
        self.show_message(text)
        self.listen_button.setEnabled(False)
        self._set_pose("talk")
        self._tts_thread = TTSThread(text, self)
        self._tts_thread.done.connect(self._on_tts_done)
        self._tts_thread.error.connect(self._on_tts_error)
        self._tts_thread.finished.connect(self._clear_tts_thread)
        self._tts_thread.start()

    def listen(self) -> None:
        if not self._mic_enabled:
            self.show_message("Microphone is disabled.")
            return
        if self._listening_thread and self._listening_thread.isRunning():
            return
        self.show_message("Listening…")
        self.listen_button.setEnabled(False)
        self._listening_thread = VoiceThread(self._mic_device_index, parent=self)
        self._listening_thread.result.connect(self._on_voice_result)
        self._listening_thread.error.connect(self._on_voice_error)
        self._listening_thread.finished.connect(self._clear_voice_thread)
        self._listening_thread.start()

    def show_message(self, text: str) -> None:
        self.bubble_label.setText(text)

    def set_idle_text(self, text: str) -> None:
        self._base_idle_text = text
        self.reset_idle()

    def reset_idle(self) -> None:
        self.show_message(self._base_idle_text)
        if self._mic_enabled and self.player.state() != QMediaPlayer.PlayingState:
            self.listen_button.setEnabled(True)
        self._update_pose()

    def set_mic_enabled(self, enabled: bool) -> None:
        self._mic_enabled = enabled
        if not enabled:
            self.listen_button.setEnabled(False)
            self.show_message("Microphone disabled")
        else:
            if self.player.state() != QMediaPlayer.PlayingState:
                self.listen_button.setEnabled(True)
            self.show_message("Microphone enabled")

    def start_roaming(self) -> None:
        if not self.roam_timer.isActive():
            self.roam_timer.start()
        self._schedule_roam()

    def stop_roaming(self) -> None:
        self.roam_timer.stop()
        self.move_animation.stop()
        self._update_pose()

    # ------------------------------------------------------------------
    # UI + behaviour wiring
    # ------------------------------------------------------------------
    def _setup_window(self) -> None:
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.resize(320, 380)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(12)

        self.character_label = QLabel(self)
        self.character_label.setFixedSize(220, 240)
        self.character_label.setScaledContents(True)
        layout.addWidget(self.character_label, alignment=Qt.AlignCenter)

        self.bubble_label = QLabel(self._base_idle_text, self)
        self.bubble_label.setWordWrap(True)
        self.bubble_label.setAlignment(Qt.AlignCenter)
        self.bubble_label.setStyleSheet(
            """
            QLabel {
                background-color: rgba(24, 24, 32, 230);
                color: white;
                border-radius: 18px;
                padding: 12px 16px;
                border: 1px solid rgba(255, 255, 255, 0.18);
            }
            """
        )
        layout.addWidget(self.bubble_label, alignment=Qt.AlignCenter)

        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        button_row.addSpacerItem(QSpacerItem(12, 12, QSizePolicy.Expanding, QSizePolicy.Minimum))

        self.listen_button = QPushButton("🎙️ Talk to me", self)
        self.listen_button.setCursor(Qt.PointingHandCursor)
        self.listen_button.setStyleSheet(
            """
            QPushButton {
                background-color: #4f46e5;
                color: white;
                border-radius: 16px;
                padding: 10px 20px;
                font-weight: 600;
            }
            QPushButton:disabled {
                background-color: rgba(255, 255, 255, 0.25);
                color: rgba(255, 255, 255, 0.65);
            }
            """
        )
        self.listen_button.clicked.connect(self.listen)
        button_row.addWidget(self.listen_button)

        self.settings_button = QPushButton("⚙️", self)
        self.settings_button.setCursor(Qt.PointingHandCursor)
        self.settings_button.setFixedSize(40, 40)
        self.settings_button.setStyleSheet(
            """
            QPushButton {
                background-color: rgba(79, 70, 229, 0.22);
                color: white;
                border-radius: 20px;
                font-size: 18px;
            }
            QPushButton:hover {
                background-color: rgba(79, 70, 229, 0.35);
            }
            """
        )
        self.settings_button.clicked.connect(self._open_settings_dialog)
        button_row.addWidget(self.settings_button)

        button_row.addSpacerItem(QSpacerItem(12, 12, QSizePolicy.Expanding, QSizePolicy.Minimum))
        layout.addLayout(button_row)

    def _setup_animation(self) -> None:
        self._frames: Dict[str, List[QPixmap]] = self._build_character_frames(220)
        self._pose = "idle"
        self._frame_index = 0
        self.character_label.setPixmap(self._frames[self._pose][self._frame_index])

        self._animation_timer = QTimer(self)
        self._animation_timer.setInterval(160)
        self._animation_timer.timeout.connect(self._advance_frame)
        self._animation_timer.start()

    def _setup_motion(self) -> None:
        self.move_animation = QPropertyAnimation(self, b"pos", self)
        self.move_animation.setEasingCurve(QEasingCurve.InOutQuad)
        self.move_animation.finished.connect(self._update_pose)
        self.roam_timer = QTimer(self)
        self.roam_timer.setInterval(9000)
        self.roam_timer.timeout.connect(self._schedule_roam)
        QTimer.singleShot(1500, self.start_roaming)

    def _setup_audio(self) -> None:
        self.player = QMediaPlayer(self)
        self.player.setVolume(100)
        self.player.stateChanged.connect(self._on_player_state_changed)
        _, active = self._get_audio_output_options()
        self._audio_output_id = active

    # ------------------------------------------------------------------
    # Events & interactions
    # ------------------------------------------------------------------
    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        menu = QMenu(self)
        vision_action = menu.addAction("Toggle screen vision")
        mic_text = "Disable microphone" if self._mic_enabled else "Enable microphone"
        mic_action = menu.addAction(mic_text)
        audio_action = menu.addAction("Audio settings")
        menu.addSeparator()
        quit_action = menu.addAction("Quit Nova")
        chosen = menu.exec_(event.globalPos())
        if chosen == vision_action:
            self.toggle_vision_requested.emit()
        elif chosen == mic_action:
            self.toggle_mic_requested.emit()
        elif chosen == audio_action:
            self._open_settings_dialog()
        elif chosen == quit_action:
            QApplication.quit()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_offset = event.globalPos() - self.pos()
            self.move_animation.stop()
            self._set_pose("walk")
            event.accept()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._dragging and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton and self._dragging:
            self._dragging = False
            event.accept()
            self._update_pose()
            return
        super().mouseReleaseEvent(event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._cleanup_audio()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Voice + audio callbacks
    # ------------------------------------------------------------------
    def _on_voice_result(self, text: str) -> None:
        self.show_message(f"You said: {text}")
        self.voice_captured.emit(text)

    def _on_voice_error(self, err: str) -> None:
        if err == LISTEN_TIMEOUT_SENTINEL:
            friendly = "I didn’t hear anything. Try speaking a little louder."
        else:
            friendly = f"Mic error: {err}"
        self.show_message(friendly)
        self.voice_error.emit(err)
        if self._mic_enabled and self.player.state() != QMediaPlayer.PlayingState:
            self.listen_button.setEnabled(True)
        self._update_pose()

    def _clear_voice_thread(self) -> None:
        self._listening_thread = None
        if self._mic_enabled and self.player.state() != QMediaPlayer.PlayingState:
            self.listen_button.setEnabled(True)

    def _on_tts_done(self, tmp_path: str) -> None:
        self._current_tmp_path = tmp_path
        self.player.setMedia(QMediaContent(QUrl.fromLocalFile(tmp_path)))
        self.player.play()
        self._set_pose("talk")

    def _on_tts_error(self, err: str) -> None:
        self.show_message(f"TTS error: {err}")
        if self._mic_enabled:
            self.listen_button.setEnabled(True)
        self._update_pose()
        self.response_finished.emit()

    def _clear_tts_thread(self) -> None:
        self._tts_thread = None

    def _on_player_state_changed(self, state: QMediaPlayer.State) -> None:
        if state == QMediaPlayer.PlayingState:
            self.listen_button.setEnabled(False)
            self._set_pose("talk")
        elif state == QMediaPlayer.StoppedState:
            self._cleanup_audio()
            if self._mic_enabled:
                self.listen_button.setEnabled(True)
            self.reset_idle()
            self.response_finished.emit()

    # ------------------------------------------------------------------
    # Floating motion helpers
    # ------------------------------------------------------------------
    def _schedule_roam(self) -> None:
        if not self.isVisible() or self._dragging:
            return
        target = self._random_position()
        if target is None:
            return
        self.move_animation.stop()
        self.move_animation.setDuration(random.randint(4500, 9000))
        self.move_animation.setStartValue(self.pos())
        self.move_animation.setEndValue(target)
        self.move_animation.start()
        self._update_pose()

    def _random_position(self) -> Optional[QPoint]:
        if QApplication.instance() is None:
            return None
        center = self.pos() + QPoint(self.width() // 2, self.height() // 2)
        screen = QApplication.screenAt(center)
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return None
        geom = screen.availableGeometry()
        max_x = geom.right() - self.width()
        max_y = geom.bottom() - self.height()
        if max_x <= geom.left() or max_y <= geom.top():
            return None
        target_x = random.randint(geom.left(), max_x)
        target_y = random.randint(geom.top(), max_y)
        return QPoint(target_x, target_y)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _cleanup_audio(self) -> None:
        if self._current_tmp_path and os.path.exists(self._current_tmp_path):
            try:
                os.remove(self._current_tmp_path)
            except OSError:
                pass
        self._current_tmp_path = None

    def _open_settings_dialog(self) -> None:
        mic_names = self._safe_list_microphones()
        outputs, active = self._get_audio_output_options()
        dialog = DeviceSettingsDialog(
            microphone_names=mic_names,
            current_mic_index=self._mic_device_index,
            audio_outputs=outputs,
            current_output_id=self._audio_output_id or active,
            parent=self,
        )
        if dialog.exec_() == QDialog.Accepted:
            new_mic = dialog.selected_mic_index()
            new_output = dialog.selected_output_id()
            self._mic_device_index = new_mic
            self._apply_audio_output(new_output)
            self.show_message("Audio settings updated")
            self._update_pose()

    def _safe_list_microphones(self) -> List[str]:
        try:
            return sr.Microphone.list_microphone_names()
        except Exception:
            return []

    def _get_audio_output_options(self) -> Tuple[List[Tuple[str, str]], Optional[str]]:
        service = self.player.service()
        if service is None:
            return [], None
        control = service.requestControl(AUDIO_OUTPUT_CONTROL_IID)
        if control is None:
            return [], None
        assert isinstance(control, QAudioOutputSelectorControl)
        try:
            default_id = control.defaultOutput()
            options: List[Tuple[str, str]] = []
            if default_id:
                default_desc = control.outputDescription(default_id)
                options.append((default_id, f"System default ({default_desc})"))
            for device_id in control.availableOutputs():
                if device_id == default_id:
                    continue
                options.append((device_id, control.outputDescription(device_id)))
            active = control.activeOutput()
            return options, active
        finally:
            service.releaseControl(control)

    def _apply_audio_output(self, device_id: Optional[str]) -> None:
        service = self.player.service()
        if service is None:
            self._audio_output_id = device_id
            return
        control = service.requestControl(AUDIO_OUTPUT_CONTROL_IID)
        if control is None:
            self._audio_output_id = device_id
            return
        assert isinstance(control, QAudioOutputSelectorControl)
        try:
            target = device_id
            if target is None:
                target = control.defaultOutput()
            if target:
                control.setActiveOutput(target)
                self._audio_output_id = control.activeOutput()
            else:
                self._audio_output_id = None
        finally:
            service.releaseControl(control)

    def _advance_frame(self) -> None:
        frames = self._frames.get(self._pose)
        if not frames:
            return
        if len(frames) == 1:
            self.character_label.setPixmap(frames[0])
            return
        self._frame_index = (self._frame_index + 1) % len(frames)
        self.character_label.setPixmap(frames[self._frame_index])

    def _set_pose(self, pose: str) -> None:
        pose = pose if pose in self._frames else "idle"
        if self._pose == pose:
            return
        self._pose = pose
        self._frame_index = 0
        self.character_label.setPixmap(self._frames[self._pose][self._frame_index])

    def _update_pose(self) -> None:
        if self.player.state() == QMediaPlayer.PlayingState:
            self._set_pose("talk")
        elif self.move_animation.state() == QAbstractAnimation.Running or self._dragging:
            self._set_pose("walk")
        else:
            self._set_pose("idle")

    def _build_character_frames(self, size: int) -> Dict[str, List[QPixmap]]:
        def build_frame(arm_phase: float, leg_phase: float, mouth_open: float) -> QPixmap:
            return self._draw_character(size, arm_phase, leg_phase, mouth_open)

        walk_frames = [
            build_frame(arm_phase=0.9, leg_phase=-0.9, mouth_open=0.15),
            build_frame(arm_phase=0.4, leg_phase=-0.4, mouth_open=0.18),
            build_frame(arm_phase=-0.9, leg_phase=0.9, mouth_open=0.15),
            build_frame(arm_phase=-0.4, leg_phase=0.4, mouth_open=0.18),
        ]
        talk_frames = [
            build_frame(arm_phase=0.05, leg_phase=0.05, mouth_open=0.12),
            build_frame(arm_phase=-0.05, leg_phase=-0.05, mouth_open=0.45),
        ]
        idle_frame = build_frame(arm_phase=0.0, leg_phase=0.0, mouth_open=0.18)
        return {
            "idle": [idle_frame],
            "walk": walk_frames,
            "talk": talk_frames,
        }

    @staticmethod
    def _draw_character(size: int, arm_phase: float, leg_phase: float, mouth_open: float) -> QPixmap:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        scale = size / 280.0
        center_x = size / 2
        head_radius = 42 * scale
        head_center_y = 70 * scale
        body_top = head_center_y + head_radius - 4 * scale
        body_bottom = size - 40 * scale
        shoulder_width = 42 * scale
        hip_width = 34 * scale

        skin_color = QColor(252, 231, 243)
        hair_color = QColor(79, 70, 229)
        outfit_color = QColor(56, 189, 248)
        accent_color = QColor(14, 165, 233)

        # Hair backdrop
        painter.setBrush(hair_color)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(
            int(center_x - head_radius * 1.05),
            int(head_center_y - head_radius * 1.05),
            int(head_radius * 2.1),
            int(head_radius * 2.05),
        )

        # Head
        painter.setBrush(skin_color)
        painter.drawEllipse(
            int(center_x - head_radius),
            int(head_center_y - head_radius),
            int(head_radius * 2),
            int(head_radius * 2),
        )

        # Eyes
        eye_radius = 6.2 * scale
        eye_offset_x = 16 * scale
        eye_y = head_center_y + 6 * scale
        painter.setBrush(Qt.white)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(
            int(center_x - eye_offset_x - eye_radius),
            int(eye_y - eye_radius),
            int(eye_radius * 2),
            int(eye_radius * 2),
        )
        painter.drawEllipse(
            int(center_x + eye_offset_x - eye_radius),
            int(eye_y - eye_radius),
            int(eye_radius * 2),
            int(eye_radius * 2),
        )

        pupil_radius = 3.2 * scale
        painter.setBrush(QColor(30, 41, 59))
        painter.drawEllipse(
            int(center_x - eye_offset_x - pupil_radius),
            int(eye_y - pupil_radius),
            int(pupil_radius * 2),
            int(pupil_radius * 2),
        )
        painter.drawEllipse(
            int(center_x + eye_offset_x - pupil_radius),
            int(eye_y - pupil_radius),
            int(pupil_radius * 2),
            int(pupil_radius * 2),
        )

        # Mouth
        mouth_width = 28 * scale
        mouth_height = max(6 * scale, mouth_open * 28 * scale)
        mouth_y = head_center_y + head_radius * 0.7
        painter.setBrush(QColor(244, 114, 182))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(
            int(center_x - mouth_width / 2),
            int(mouth_y - mouth_height / 2),
            int(mouth_width),
            int(mouth_height),
            8 * scale,
            8 * scale,
        )

        # Neck
        neck_width = 20 * scale
        neck_height = 16 * scale
        painter.setBrush(skin_color)
        painter.drawRoundedRect(
            int(center_x - neck_width / 2),
            int(body_top - neck_height),
            int(neck_width),
            int(neck_height + 2 * scale),
            6 * scale,
            6 * scale,
        )

        # Body / outfit
        painter.setBrush(outfit_color)
        painter.setPen(Qt.NoPen)
        body_width = 110 * scale
        painter.drawRoundedRect(
            int(center_x - body_width / 2),
            int(body_top),
            int(body_width),
            int(body_bottom - body_top - 10 * scale),
            26 * scale,
            26 * scale,
        )

        # Waist accent
        painter.setBrush(accent_color)
        painter.drawRoundedRect(
            int(center_x - body_width / 2),
            int(body_bottom - 70 * scale),
            int(body_width),
            int(18 * scale),
            14 * scale,
            14 * scale,
        )

        pen = QPen(QColor(30, 64, 175))
        pen.setWidthF(5 * scale)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)

        shoulder_y = body_top + 30 * scale
        hand_y = shoulder_y + 68 * scale
        hip_y = body_bottom - 38 * scale
        foot_y = body_bottom + 12 * scale

        arm_swing = 24 * scale * arm_phase
        leg_swing = 26 * scale * leg_phase

        # Arms
        left_shoulder = QPoint(int(center_x - shoulder_width), int(shoulder_y))
        right_shoulder = QPoint(int(center_x + shoulder_width), int(shoulder_y))
        left_hand = QPoint(int(center_x - shoulder_width - arm_swing), int(hand_y))
        right_hand = QPoint(int(center_x + shoulder_width - arm_swing), int(hand_y))
        painter.drawLine(left_shoulder, left_hand)
        painter.drawLine(right_shoulder, right_hand)

        # Legs
        left_hip = QPoint(int(center_x - hip_width), int(hip_y))
        right_hip = QPoint(int(center_x + hip_width), int(hip_y))
        left_foot = QPoint(int(center_x - hip_width - leg_swing), int(foot_y))
        right_foot = QPoint(int(center_x + hip_width - leg_swing), int(foot_y))
        painter.drawLine(left_hip, left_foot)
        painter.drawLine(right_hip, right_foot)

        painter.end()
        return pixmap


if __name__ == "__main__":
    import sys

    app = QApplication(sys.argv)
    avatar = Avatar()
    avatar.show()
    sys.exit(app.exec_())
