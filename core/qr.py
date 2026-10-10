import logging
from pathlib import Path
from typing import Optional

from PIL import Image


class QRCodeScanner:
    """Detects and decodes QR codes from images."""

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self._logger = logger or logging.getLogger(__name__)

    def scan(self, image_path: Path) -> Optional[str]:
        """Scans image for QR code. Returns decoded content or None."""
        if not image_path.is_file():
            return None

        try:
            import cv2
            import numpy as np

            # Read image with OpenCV
            img = cv2.imread(str(image_path))
            if img is None:
                return None

            detector = cv2.QRCodeDetector()
            data, bbox, _ = detector.detectAndDecode(img)
            if data and data.strip():
                self._logger.info("QR-код успешно декодирован: %s", data.strip())
                return data.strip()
        except ImportError:
            self._logger.debug("OpenCV не установлен, пропуск QR-детекции.")
        except Exception as exc:
            self._logger.warning("Ошибка при сканировании QR-кода: %s", exc)

        return None
