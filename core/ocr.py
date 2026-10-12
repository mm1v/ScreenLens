import asyncio
import logging
import os
import shutil
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from PIL import Image, ImageEnhance
import pytesseract

from core.models import OCRResult


class OCRError(Exception):
    """Base exception for OCR-related errors."""


class TesseractNotFoundError(OCRError):
    """Raised when the Tesseract OCR executable cannot be found on the system."""


class BaseOCREngine(ABC):
    """Abstract base class for OCR engines."""

    @abstractmethod
    def recognize(self, image_path: Path) -> OCRResult:
        """Extract text from the specified image file."""


class TesseractOCREngine(BaseOCREngine):
    """Local OCR engine powered by Tesseract OCR."""

    DEFAULT_WINDOWS_PATHS = (
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
        Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
        Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe"),
    )

    def __init__(
        self,
        tesseract_cmd: Optional[str] = None,
        language: str = "rus+eng",
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._logger = logger or logging.getLogger(__name__)
        self._language = language
        self._tesseract_path = self._resolve_tesseract_path(tesseract_cmd)
        pytesseract.pytesseract.tesseract_cmd = str(self._tesseract_path)
        self._effective_lang = self._validate_and_select_language(self._language)

    def _resolve_tesseract_path(self, explicit_path: Optional[str]) -> Path:
        """Finds Tesseract executable or raises informative TesseractNotFoundError."""
        # 1. Explicit path from argument
        if explicit_path:
            p = Path(explicit_path)
            if p.is_file():
                return p
            raise TesseractNotFoundError(
                f"Указанный путь к Tesseract не найден: '{explicit_path}'"
            )

        # 2. Environment variable
        env_path = os.environ.get("TESSERACT_PATH")
        if env_path:
            p = Path(env_path)
            if p.is_file():
                return p

        # 3. Standard Windows directories
        for candidate in self.DEFAULT_WINDOWS_PATHS:
            if candidate.is_file():
                return candidate

        # 4. In system PATH
        which_path = shutil.which("tesseract")
        if which_path:
            return Path(which_path)

        raise TesseractNotFoundError(
            "Tesseract OCR не найден в системе!\n"
            "Для работы OCR требуется установленный бинарный файл Tesseract.\n\n"
            "Инструкция по установке на Windows:\n"
            "  1. Через winget:\n"
            "     winget install UB-Mannheim.TesseractOCR\n\n"
            "  2. Или скачайте установщик вручную:\n"
            "     https://github.com/UB-Mannheim/tesseract/wiki\n"
            "     (При установке выберите 'Additional language data (download)' -> Russian).\n\n"
            "  3. Если Tesseract установлен в нестандартную папку, передайте путь:\n"
            "     python main.py --tesseract-path \"C:\\Path\\To\\tesseract.exe\"\n"
            "     или задайте переменную окружения TESSERACT_PATH."
        )

    def _validate_and_select_language(self, requested_lang: str) -> str:
        """Validates requested language against installed traineddata files."""
        try:
            available = pytesseract.get_languages(config="")
        except Exception as exc:
            self._logger.warning("Could not query available Tesseract languages: %s", exc)
            return requested_lang

        parts = [lang.strip() for lang in requested_lang.split("+")]
        valid_parts = [p for p in parts if p in available]

        if not valid_parts:
            fallback = "eng" if "eng" in available else (available[0] if available else "eng")
            self._logger.warning(
                "Языковые пакеты '%s' не найдены. Используется '%s'.",
                requested_lang,
                fallback,
            )
            return fallback

        if len(valid_parts) < len(parts):
            missing = set(parts) - set(valid_parts)
            self._logger.warning(
                "Некоторые языковые пакеты отсутствуют: %s. Используются: %s",
                missing,
                "+".join(valid_parts),
            )

        return "+".join(valid_parts)

    def _preprocess_image(self, image: Image.Image) -> Image.Image:
        """Enhances image for improved OCR accuracy."""
        gray = image.convert("L")
        enhancer = ImageEnhance.Contrast(gray)
        enhanced = enhancer.enhance(1.8)

        if enhanced.width < 300 or enhanced.height < 300:
            scale = 2
            enhanced = enhanced.resize(
                (enhanced.width * scale, enhanced.height * scale),
                Image.Resampling.BICUBIC,
            )

        return enhanced

    def recognize(self, image_path: Path) -> OCRResult:
        """Performs OCR recognition using Tesseract."""
        if not image_path.is_file():
            raise FileNotFoundError(f"Файл изображения не найден: {image_path}")

        try:
            with Image.open(image_path) as img:
                img.load()
                processed_img = self._preprocess_image(img.copy())
                raw_text = pytesseract.image_to_string(
                    processed_img,
                    lang=self._effective_lang,
                    config="--psm 3",
                )
        except Exception as exc:
            self._logger.error("OCR recognition error: %s", exc)
            raise OCRError(f"Ошибка распознавания текста Tesseract: {exc}") from exc

        raw_text = _correct_ocr_confusions(raw_text)
        cleaned_text = "\n".join(
            line.strip() for line in raw_text.splitlines() if line.strip()
        )

        import re
        cyr_count = len(re.findall(r"[\u0400-\u04FF]", raw_text))
        lat_count = len(re.findall(r"[a-zA-Z]", raw_text))
        if cyr_count > 0 and lat_count > 0:
            det_lang = "rus+eng"
        elif cyr_count > 0:
            det_lang = "ru"
        elif lat_count > 0:
            det_lang = "en"
        else:
            det_lang = self._effective_lang

        return OCRResult(
            raw_text=raw_text,
            cleaned_text=cleaned_text,
            image_path=image_path,
            language=det_lang,
        )


HOMOGLYPHS_CYR_TO_LAT = {
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "Х": "X",
}
HOMOGLYPHS_LAT_TO_CYR = {v: k for k, v in HOMOGLYPHS_CYR_TO_LAT.items()}


def _normalize_intra_word_homoglyphs(text: str) -> str:
    """Fixes accidental mixed-script words (e.g. Cyrillic word with 1 Latin letter or vice-versa)."""
    import re
    tokens = re.split(r"(\s+)", text)
    result = []
    for token in tokens:
        if not token or token.isspace():
            result.append(token)
            continue
        m = re.match(r"^([^a-zA-Z\u0400-\u04ff]*)(.*?)([^a-zA-Z\u0400-\u04ff]*)$", token)
        if not m:
            result.append(token)
            continue
        prefix, core, suffix = m.groups()
        if not core:
            result.append(token)
            continue
        cyr = len(re.findall(r"[\u0400-\u04ff]", core))
        lat = len(re.findall(r"[a-zA-Z]", core))
        if cyr > 0 and lat > 0:
            if cyr >= lat:
                core = "".join(HOMOGLYPHS_LAT_TO_CYR.get(c, c) for c in core)
            else:
                core = "".join(HOMOGLYPHS_CYR_TO_LAT.get(c, c) for c in core)
        result.append(prefix + core + suffix)
    return "".join(result)


def _correct_ocr_confusions(text: str) -> str:
    """Post-processes recognized text to fix common OCR confusions like 0 vs O/o and intra-word homoglyphs."""
    import re

    # 0 at start of word before letters: 0verview -> Overview
    text = re.sub(r"\b0([a-zа-я]{2,})", r"O\1", text)
    text = re.sub(r"\b0([A-ZА-Я]{2,})", r"O\1", text)
    # 0 inside word: c0mputer -> computer, сл0во -> слово
    text = re.sub(r"(?<=[a-zA-Zа-яА-Я])0(?=[a-zA-Zа-яА-Я])", "o", text)
    # 0 at end of word: inf0 -> info
    text = re.sub(r"([a-zA-Zа-яА-Я]{2,})0\b", r"\g<1>o", text)

    # O/o inside numbers: 1O5 -> 105, 2o24 -> 2024
    text = re.sub(r"(?<=\d)[Oo](?=\d)", "0", text)
    # O/o at end of number: 123O -> 1230
    text = re.sub(r"\b(\d{2,})[Oo]\b", r"\g<1>0", text)
    # O/o at start of number: O123 -> 0123
    text = re.sub(r"\b[Oo](\d{2,})\b", r"0\1", text)

    text = _normalize_intra_word_homoglyphs(text)
    return text



class WindowsMediaOCREngine(BaseOCREngine):
    """Native Windows 10/11 OCR engine using Windows.Media.Ocr API with dual language & enhancement."""


    def __init__(
        self,
        language: str = "rus+eng",
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._logger = logger or logging.getLogger(__name__)
        self._raw_lang = language.lower()

    def _preprocess_image(self, img: Image.Image) -> Image.Image:
        """Optimized image preprocessing for OCR: bounds large dimensions to <= 2200px, guarantees min 40x40 px."""
        w, h = img.size

        # Cap oversized images to prevent Windows Media OCR 2600px crash and memory bloat
        max_dim = max(w, h)
        if max_dim > 2200:
            scale = 2200.0 / max_dim
            new_w = max(1, int(w * scale))
            new_h = max(1, int(h * scale))
            img = img.resize((new_w, new_h), Image.Resampling.BILINEAR)
            w, h = new_w, new_h

        # Windows Media OCR requires minimum 40x40 pixels; upscale narrow/short crops
        min_dim = min(w, h)
        if min_dim < 50:
            scale = max(2.0, 60.0 / max(min_dim, 1))
            new_w = max(45, int(w * scale))
            new_h = max(45, int(h * scale))
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            w, h = new_w, new_h

        # Only upscale small crops (< 90 px height) to avoid RAM bloat while keeping small text sharp
        if h < 90:
            scale = min(3, max(2, int(150 / max(h, 1))))
            scaled = img.resize((w * scale, h * scale), Image.Resampling.LANCZOS)
            enhancer = ImageEnhance.Contrast(scaled)
            return enhancer.enhance(1.4)

        return img

    CYR_NON_HOMOGLYPHS = set("бвгджзийклмнптфцчшщъыьэюяБГДЁЖЗИЙЛПУФЦЧШЩЪЫЬЭЮЯ")

    @staticmethod
    def _get_line_bbox(line: dict) -> tuple[float, float, float, float]:
        words = line.get("words", [])
        if not words:
            return 0.0, 0.0, 0.0, 0.0
        y = min(w.get("bounding_rect", {}).get("y", 0.0) for w in words)
        x = min(w.get("bounding_rect", {}).get("x", 0.0) for w in words)
        max_y = max(w.get("bounding_rect", {}).get("y", 0.0) + w.get("bounding_rect", {}).get("height", 0.0) for w in words)
        max_x = max(w.get("bounding_rect", {}).get("x", 0.0) + w.get("bounding_rect", {}).get("width", 0.0) for w in words)
        return x, y, max_x - x, max_y - y

    def _restructure_lines_by_layout(self, res: Optional[dict]) -> dict:
        """Groups fragmented or multi-column OCR bounding boxes into visual lines in natural reading order."""
        if not isinstance(res, dict):
            return res if res is not None else {}
        lines = res.get("lines", [])
        if not lines:
            return res

        items = []
        for l in lines:
            b = self._get_line_bbox(l)
            items.append((b, l))

        # Sort primarily by vertical coordinate y
        items.sort(key=lambda item: item[0][1])

        # Cluster boxes into visual horizontal rows where vertical overlap >= 45% of line height
        rows = []
        for b, l in items:
            x, y, w, h = b
            placed = False
            for row in rows:
                row_y_min = min(item[0][1] for item in row)
                row_y_max = max(item[0][1] + item[0][3] for item in row)
                overlap = min(y + h, row_y_max) - max(y, row_y_min)
                min_h = min(h, max(item[0][3] for item in row))
                if min_h > 0 and (overlap / min_h) >= 0.45:
                    row.append((b, l))
                    placed = True
                    break
            if not placed:
                rows.append([(b, l)])

        # Sort rows top-to-bottom by row vertical position
        rows.sort(key=lambda row: min(item[0][1] for item in row))

        new_lines = []
        for row in rows:
            # Sort fragments in row left-to-right by x
            row.sort(key=lambda item: item[0][0])
            merged_words = []
            for _, l in row:
                merged_words.extend(l.get("words", []))
            merged_words.sort(key=lambda w: w.get("bounding_rect", {}).get("x", 0.0))
            merged_text = " ".join(w.get("text", "") for w in merged_words if w.get("text", "")).strip()
            new_lines.append({
                "text": merged_text,
                "words": merged_words,
            })
        return {"lines": new_lines, "text": "\n".join(l["text"] for l in new_lines)}

    def _extract_lines_text(self, res: Optional[dict]) -> str:
        if not isinstance(res, dict):
            return getattr(res, "text", "") or ""
        restructured = self._restructure_lines_by_layout(res)
        lines = restructured.get("lines", [])
        if lines:
            return "\n".join(l.get("text", "") for l in lines if l.get("text"))
        return res.get("text", "")

    def _merge_dual_language_results(self, res_ru: Optional[dict], res_en: Optional[dict]) -> str:
        import re
        res_ru = self._restructure_lines_by_layout(res_ru)
        res_en = self._restructure_lines_by_layout(res_en)

        ru_lines = res_ru.get("lines", []) if isinstance(res_ru, dict) else []
        en_lines = res_en.get("lines", []) if isinstance(res_en, dict) else []

        if not ru_lines and not en_lines:
            return ""
        if not ru_lines:
            return self._extract_lines_text(res_en)
        if not en_lines:
            return self._extract_lines_text(res_ru)

        output_items: list[tuple[float, str]] = []
        matched_en_indices: set[int] = set()

        for l_ru in ru_lines:
            x_ru, y_ru, w_ru, h_ru = self._get_line_bbox(l_ru)

            best_en_idx: Optional[int] = None
            best_overlap = 0.0
            for idx, l_en in enumerate(en_lines):
                if idx in matched_en_indices:
                    continue
                x_en, y_en, w_en, h_en = self._get_line_bbox(l_en)
                overlap_y = max(0.0, min(y_ru + h_ru, y_en + h_en) - max(y_ru, y_en))
                if overlap_y > best_overlap:
                    best_overlap = overlap_y
                    best_en_idx = idx

            matched_en = en_lines[best_en_idx] if (best_en_idx is not None and best_overlap > max(4.0, h_ru * 0.35)) else None
            if matched_en and best_en_idx is not None:
                matched_en_indices.add(best_en_idx)

            text_ru = l_ru.get("text", "")
            text_en = matched_en.get("text", "") if matched_en else ""

            # If line in RU has NO genuine Cyrillic letters (e.g. pure English sentence, code, URL):
            has_genuine_cyr = any(c in self.CYR_NON_HOMOGLYPHS for c in text_ru)
            if not has_genuine_cyr and text_en:
                output_items.append((y_ru, text_en))
                continue

            # If line has genuine Cyrillic letters, check word by word to preserve embedded Latin tokens
            if matched_en and matched_en.get("words"):
                words_ru = l_ru.get("words", [])
                words_en = matched_en.get("words", [])
                fixed_words: list[str] = []
                used_en_word_indices: set[int] = set()

                for wr in words_ru:
                    w_text = wr.get("text", "")
                    if any(c in self.CYR_NON_HOMOGLYPHS for c in w_text):
                        fixed_words.append(w_text)
                        continue

                    # Pure numbers or punctuation without letters should not be replaced with Latin words
                    if not re.search(r"[a-zA-Z\u0400-\u04ff]", w_text):
                        fixed_words.append(w_text)
                        continue

                    # Word has no genuine Cyrillic: find closest unused EN word by spatial coordinates
                    bx = wr.get("bounding_rect", {})
                    w_x = bx.get("x", 0.0)
                    best_we_idx = None
                    best_dist = 99999.0

                    for we_idx, we in enumerate(words_en):
                        if we_idx in used_en_word_indices:
                            continue
                        we_x = we.get("bounding_rect", {}).get("x", 0.0)
                        dist = abs(w_x - we_x)
                        if dist < best_dist:
                            best_dist = dist
                            best_we_idx = we_idx

                    max_allowed_dist = max(18.0, bx.get("width", 20.0) * 0.8)
                    if best_we_idx is not None and best_dist <= max_allowed_dist:
                        we_text = words_en[best_we_idx].get("text", "")
                        if re.search(r"[a-zA-Z]", we_text):
                            fixed_words.append(we_text)
                            used_en_word_indices.add(best_we_idx)
                        else:
                            fixed_words.append(w_text)
                    else:
                        fixed_words.append(w_text)
                output_items.append((y_ru, " ".join(fixed_words)))
            else:
                output_items.append((y_ru, text_ru))

        # Include un-matched EN lines at their proper vertical position
        for idx, l_en in enumerate(en_lines):
            if idx not in matched_en_indices:
                t = l_en.get("text", "").strip()
                if t:
                    _, y_en, _, _ = self._get_line_bbox(l_en)
                    output_items.append((y_en, t))

        output_items.sort(key=lambda item: item[0])
        return "\n".join(item[1] for item in output_items)

    def recognize(self, image_path: Path) -> OCRResult:
        """Performs OCR recognition using native Windows Media OCR API."""
        if not image_path.is_file():
            raise FileNotFoundError(f"Файл изображения не найден: {image_path}")

        try:
            import winocr
        except ImportError as exc:
            raise OCRError(
                "Пакет 'winocr' не установлен. Установите: pip install winocr"
            ) from exc

        try:
            with Image.open(image_path) as orig_img:
                orig_img.load()
                img = self._preprocess_image(orig_img.copy())

            # Check available system tags
            available_tags = [l.language_tag for l in winocr.OcrEngine.available_recognizer_languages]
            en_tag = "en-US" if "en-US" in available_tags else ("en" if "en" in available_tags else None)
            ru_tag = "ru" if "ru" in available_tags else None

            raw_text = ""

            if ("rus" in self._raw_lang or "ru" in self._raw_lang) and ("eng" in self._raw_lang or "en" in self._raw_lang):
                res_ru = winocr.recognize_pil_sync(img, ru_tag) if ru_tag else None
                res_en = winocr.recognize_pil_sync(img, en_tag) if en_tag else None
                raw_text = self._merge_dual_language_results(res_ru, res_en)
            elif "eng" in self._raw_lang or "en" in self._raw_lang:
                tag = en_tag or "en-US"
                res = winocr.recognize_pil_sync(img, tag)
                raw_text = self._extract_lines_text(res)
            else:
                tag = ru_tag or "ru"
                res = winocr.recognize_pil_sync(img, tag)
                raw_text = self._extract_lines_text(res)

            raw_text = _correct_ocr_confusions(raw_text)

            import re
            cyr_count = len(re.findall(r"[\u0400-\u04FF]", raw_text))
            lat_count = len(re.findall(r"[a-zA-Z]", raw_text))
            if cyr_count > 0 and lat_count > 0:
                detected_lang = "rus+eng"
            elif cyr_count > 0:
                detected_lang = "ru"
            elif lat_count > 0:
                detected_lang = "en"
            else:
                detected_lang = "ru"

        except Exception as exc:
            self._logger.error("Windows Media OCR recognition error: %s", exc)
            raise OCRError(f"Ошибка распознавания текста Windows OCR: {exc}") from exc

        cleaned_text = "\n".join(
            line.strip() for line in raw_text.splitlines() if line.strip()
        )

        return OCRResult(
            raw_text=raw_text,
            cleaned_text=cleaned_text,
            image_path=image_path,
            language=detected_lang,
        )


def create_ocr_engine(
    engine_name: str = "auto",
    tesseract_cmd: Optional[str] = None,
    language: str = "rus+eng",
    logger: Optional[logging.Logger] = None,
) -> BaseOCREngine:
    """Factory creating appropriate OCR engine instance based on preference and availability."""
    log = logger or logging.getLogger(__name__)

    if engine_name == "winocr":
        log.info("Инициализация нативного Windows Media OCR движка...")
        return WindowsMediaOCREngine(language=language, logger=log)

    if engine_name == "tesseract":
        log.info("Инициализация Tesseract OCR движка...")
        return TesseractOCREngine(
            tesseract_cmd=tesseract_cmd,
            language=language,
            logger=log,
        )

    # Auto mode: try Tesseract, fallback to native Windows Media OCR if available
    try:
        engine = TesseractOCREngine(
            tesseract_cmd=tesseract_cmd,
            language=language,
            logger=log,
        )
        log.info("Выбран OCR движок: Tesseract OCR")
        return engine
    except TesseractNotFoundError:
        if sys.platform == "win32":
            try:
                import winocr  # noqa: F401
                log.info(
                    "Tesseract не найден, используется нативный Windows Media OCR движок."
                )
                return WindowsMediaOCREngine(language=language, logger=log)
            except ImportError:
                pass
        # If fallback not possible, raise original Tesseract error
        raise
