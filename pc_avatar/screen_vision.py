import os
import shutil
from typing import Optional

import pyautogui

try:
    from PIL import ImageGrab  # type: ignore
except Exception:  # pragma: no cover - optional dependency issues
    ImageGrab = None  # type: ignore

try:
    import pytesseract
except ImportError:  # pragma: no cover - optional dependency
    pytesseract = None  # type: ignore[assignment]


_POSSIBLE_TESSERACT_PATHS = [
    os.environ.get("TESSERACT_PATH"),
    shutil.which("tesseract"),
    r"C:\\Program Files\\Tesseract-OCR\\tesseract.exe",
    r"C:\\Program Files (x86)\\Tesseract-OCR\\tesseract.exe",
]


def _resolve_tesseract_cmd() -> Optional[str]:
    if pytesseract is None:
        return None

    for candidate in _POSSIBLE_TESSERACT_PATHS:
        if candidate and os.path.exists(candidate):
            pytesseract.pytesseract.tesseract_cmd = candidate
            return candidate

    try:
        pytesseract.get_tesseract_version()
        return pytesseract.pytesseract.tesseract_cmd
    except (pytesseract.pytesseract.TesseractNotFoundError, OSError):
        return None


_TESSERACT_CMD = _resolve_tesseract_cmd()


def is_vision_ready() -> bool:
    """Return True when pytesseract and the Tesseract binary are available."""

    return pytesseract is not None and _TESSERACT_CMD is not None


def get_screen_text() -> Optional[str]:
    """Capture a screenshot and OCR it when Tesseract is available.

    Returns None when OCR isn’t available or when Tesseract errors.
    """

    if not is_vision_ready():
        return None

    screenshot = _capture_full_desktop()
    try:
        return pytesseract.image_to_string(screenshot)
    except pytesseract.pytesseract.TesseractNotFoundError:  # pragma: no cover - runtime env issue
        return None
    except OSError:  # pragma: no cover - surrogate for other OS-level issues
        return None


def _capture_full_desktop():
    """Grab a screenshot spanning all monitors when supported."""

    try:
        return pyautogui.screenshot(allScreens=True)
    except TypeError:
        pass
    except Exception:
        pass

    if ImageGrab is not None:
        try:
            return ImageGrab.grab(all_screens=True)
        except Exception:
            pass

    return pyautogui.screenshot()
