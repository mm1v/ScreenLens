import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.clipboard import ClipboardManager
from core.models import CaptureArea, OCRResult, ScreenshotResult
from core.ocr import create_ocr_engine
from core.qr import QRCodeScanner
from core.screenshot import ScreenGrabber
from storage.db import AppSettings


@dataclass
class ProcessResult:
    """Contains results of complete capture pipeline."""

    shot: ScreenshotResult
    ocr: OCRResult
    qr_data: Optional[str] = None
    copied_text: str = ""


class CaptureService:
    """Unified service orchestrating screenshot capture, QR scanning, and OCR processing."""

    def __init__(
        self,
        settings: Optional[AppSettings] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.settings = settings or AppSettings()
        self.logger = logger or logging.getLogger(__name__)
        self.grabber = ScreenGrabber(logger=self.logger)
        self.qr_scanner = QRCodeScanner(logger=self.logger)
        self.clipboard = ClipboardManager(logger=self.logger)

    def process_capture(
        self,
        area: CaptureArea,
        auto_copy: bool = True,
    ) -> ProcessResult:
        """Executes full capture -> QR -> OCR -> clipboard pipeline."""
        shot = self.grabber.capture(area)
        qr_data = self.qr_scanner.scan(shot.image_path)

        ocr_engine = create_ocr_engine(
            engine_name=self.settings.ocr_engine,
            language=self.settings.ocr_lang,
            logger=self.logger,
        )
        ocr = ocr_engine.recognize(shot.image_path)

        copied_text = ""
        text_to_copy = qr_data if qr_data else ocr.cleaned_text
        if auto_copy and text_to_copy:
            self.clipboard.copy(text_to_copy)
            copied_text = text_to_copy

        return ProcessResult(
            shot=shot,
            ocr=ocr,
            qr_data=qr_data,
            copied_text=copied_text,
        )
