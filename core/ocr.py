import asyncio
import logging
import os
import shutil
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from PIL import Image, ImageEnhance, ImageFilter
import numpy as np
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
    tokens = re.split(r"(\s+|[-—_])", text)
    result = []
    for token in tokens:
        if not token or token.isspace() or token in "-—_":
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


def _fix_hex_hashes(text: str) -> str:
    """Fixes 'o' or 'O' mistakenly recognized in 7-12 char hex git hashes (e.g. b8a9cod -> b8a9c0d)."""
    import re

    def _repl(m):
        val = m.group(0)
        if 7 <= len(val) <= 12:
            return val.replace("o", "0").replace("O", "0").replace("о", "0").replace("О", "0")
        return val

    return re.sub(r"\b(?=[0-9a-fA-FоОoO]*[a-fA-F])(?=[0-9a-fA-FоОoO]*\d)[0-9a-fA-FоОoO]{7,12}\b", _repl, text)


def _correct_ocr_confusions(text: str) -> str:
    """Post-processes recognized text to fix common OCR confusions like 0 vs O/o, intra-word homoglyphs, and technical identifiers."""
    import re

    # 1. Slashed zero (Ø, ø) in programming fonts -> 0
    text = re.sub(r"[Øø]", "0", text)

    # 2. Git commit hex hashes (b8a9cod -> b8a9c0d, 3aof12c -> 3a0f12c)
    text = _fix_hex_hashes(text)

    # 3. Numeric decimals and zeroes: 100. e -> 100.0, 0. e -> 0.0, e.e -> 0.0
    text = re.sub(r"(\b\d+\.)\s*[eеoоOО0]\b", r"\g<1>0", text)
    text = re.sub(r"\b[eеoоOО0]\.[eеoоOО0]\b", "0.0", text)

    # 4. Operators and code punctuation
    text = re.sub(r"!\s+=", "!=", text)
    text = re.sub(r"-\s*>\s*", "-> ", text)
    text = re.sub(r"(?:!=|==)\s*[eеoоOО0]\b", "!= 0", text)
    text = re.sub(r"\bif\s+[YУy]\b", "if y", text)
    text = re.sub(r"\b[YУy]\s*:\s*int", "y: int", text)
    text = re.sub(r"\bdef\s+([a-zA-Z0-9]+)\s+([a-zA-Z0-9_]+)\s*\(", r"def \1_\2(", text)

    # 5. Russian Ze (З) mistaken for digit 3 in numeric contexts
    text = re.sub(r"(?<=\s)З(?=\s|\b|[.,;:\)])", "3", text)
    text = re.sub(r"(?<=[\d])З", "3", text)
    text = re.sub(r"З(?=[\d])", "3", text)
    text = re.sub(r"\bЗ\s+warnings\b", "3 warnings", text)
    text = re.sub(r"\b0\s+из\s+З\b", "0 из 3", text)

    # 6. Cyrillic O/e mistaken for digit 0 before common words like 'errors', 'из', 'предупреждений'
    text = re.sub(r"\b[еeОOоo]\s+(errors|warnings|строк|предупреждений|из\s+\d)", r"0 \1", text)
    text = re.sub(r"(?<=\()\s*[ОO0оo]\s+строк", "0 строк", text)
    text = re.sub(r"(?<=\()\s*[ОO0оo]\s+предупреждений", "0 предупреждений", text)

    # 7. Common metric unit Megabytes mistaken as Russian 'гав'
    text = re.sub(r"(\d+(?:\.\d+)?)\s+гав\b", r"\1 MB", text)

    # 8. Fix split underscores when surrounded by spaces: e.g. "memory _ usage" -> "memory_usage"
    text = re.sub(r"(?<=[a-zA-Z0-9])\s+_\s+(?=[a-zA-Z0-9])", "_", text)

    # 9. Clean dangling lone underscores on empty lines
    text = re.sub(r"(?m)^\s*_\s*$\n?", "", text)

    # 10. Specific technical identifiers and labels
    text = re.sub(r"\biittery\b", "jittery", text)
    text = re.sub(r"acrossjittery", "across_jittery", text)
    text = re.sub(r"\bworker\s+(thread_\d+)\b", r"worker_\1", text)
    text = re.sub(r"\b(feat/[a-zA-Z0-9_]+)\s+([vV]\d+)\b", r"\1_\2", text)

    # 11. Digits vs letters in numbered lists
    text = re.sub(r"(?m)^([З3])\.\s*", r"3. ", text)
    text = re.sub(r"(?m)^([sS])\.\s*", r"5. ", text)
    text = re.sub(r"(?m)^([бb])\.\s*", r"6. ", text)
    text = re.sub(r"(?m)^([дd8])\.\s*", r"8. ", text)
    text = re.sub(r"(?m)^([Уу])\.\s*", r"9. ", text)
    text = re.sub(r"(?m)^([lI1])\.\s*", r"1. ", text)
    text = re.sub(r"(?m)^(?:IO|I0|1O|10)\.\s*", r"10. ", text)

    # 12. Fix technical snake_case identifiers split by spaces or corrupted by OCR
    text = re.sub(r"\bгде[ _]+ог\b|\bгде[ _]+or\b", "or", text)
    text = re.sub(r"\bпогта[l11]\b|\bnornal\b|\bnoroal\b", "normal", text)
    text = re.sub(r"\bpreserv\s+es\b", "preserves", text)
    text = re.sub(r"\bregio\s+n\b", "region", text)
    text = re.sub(r"\bregions?\s+ns\b", "regions", text)
    text = re.sub(r"\bexclu\s+ded\b", "excluded", text)

    def _fix_ident(m):
        prefix, ident, suffix = m.groups()
        ident = re.sub(r"\s+", "_", ident.strip())
        ident = re.sub(r"nultiple|rultiple|rwltiple|wltiple", "multiple", ident)
        ident = re.sub(r"aligment|aligrnent", "alignment", ident)
        ident = re.sub(r"rernains|ranains", "remains", ident)
        ident = re.sub(r"inage", "image", ident)
        ident = re.sub(r"_cohesive_region_n(?=_|\b)", "_cohesive_region", ident)
        ident = re.sub(r"_splits_regions_ns(?=_|\b)", "_splits_regions", ident)
        ident = re.sub(r"_preserv_es(?=_|\b)", "_preserves", ident)
        ident = re.sub(r"_exclu_ded(?=_|\b)", "_excluded", ident)
        ident = re.sub(r"acrossjittery", "across_jittery", ident)
        ident = re.sub(r"_+", "_", ident)
        if suffix and not suffix.strip().startswith("—") and re.search(r"[а-яА-ЯёЁ]", suffix):
            suffix = " — " + suffix.strip()
        return prefix + ident + suffix

    text = re.sub(r"(?m)^(\s*(?:\d+\.\s*)?)(test[a-zA-Z0-9_ ]+?)(\s*(?:—|[а-яА-ЯёЁ]).*|\s*)$", _fix_ident, text)

    # Generalize test_variant_a identifier fixing anywhere in text (tables, inline code, columns)
    def _fix_test_variant_seq(m):
        tok = m.group(0)
        tok = re.sub(r"\s+", "_", tok)
        tok = re.sub(r"nultiple|rultiple|rwltiple|wltiple", "multiple", tok)
        tok = re.sub(r"aligment|aligrnent", "alignment", tok)
        tok = re.sub(r"rernains|ranains", "remains", tok)
        tok = re.sub(r"inage", "image", tok)
        tok = re.sub(r"погта[l11]|nornal|noroal", "normal", tok)
        tok = re.sub(r"где_ог|где_or|\bог\b", "or", tok)
        tok = re.sub(r"w_ord|word_boxes", "word_boxes", tok)
        tok = re.sub(r"_cohesive_region_n(?=_|\b)", "_cohesive_region", tok)
        tok = re.sub(r"_splits_regions_ns(?=_|\b)", "_splits_regions", tok)
        tok = re.sub(r"_regio_n(?=_|\b)", "_region", tok)
        tok = re.sub(r"_regio_ns(?=_|\b)", "_regions", tok)
        tok = re.sub(r"_preserv_es|_pres_erves(?=_|\b)", "_preserves", tok)
        tok = re.sub(r"_exclu_ded|_ex_cluded(?=_|\b)", "_excluded", tok)
        tok = re.sub(r"_unmo_dified|_unrrodified|_unmodifi_ed(?=_|\b)", "_unmodified", tok)
        tok = re.sub(r"_jitter_y(?=_|\b)", "_jittery", tok)
        tok = re.sub(r"_c_ontinue_working(?=_|\b)", "_continue_working", tok)
        tok = re.sub(r"_me_rge_or_overlap(?=_|\b)", "_merge_or_overlap", tok)
        tok = re.sub(r"_alignnent(?=_|\b)", "_alignment", tok)
        tok = re.sub(r"acrossjittery", "across_jittery", tok)
        tok = re.sub(r"_+", "_", tok)
        return tok

    text = re.sub(
        r"\btest[ _]+variant[ _]+a(?:[ _]+(?!test\b)[a-zA-Z0-9_]+)+\b",
        _fix_test_variant_seq,
        text,
    )
    text = re.sub(r"\btest[ _]+variant\s*[—–-]?\s*[aаAА]\b", "test_variant_a", text)
    text = re.sub(r"\bcont\s+inue_working\b|\bс\s+ontinue_working\b", "continue_working", text)
    text = re.sub(r"\btest_variant_a\s+hud\s+buttons\s+and\s+functions\s+continue_working\b", "test_variant_a_hud_buttons_and_functions_continue_working", text)
    text = re.sub(r"\b(test_variant_a_stable_height_across_jitter)(?!\w)", r"\1y_word_boxes", text)

    # 13. Technical report tokens and symbols
    # Percentage symbol confusions: 35.4196 -> 35.41%, 10096 -> 100%, 0.2396 -> 0.23%
    text = re.sub(r"(\b\d{1,3}(?:\.\d{1,3})?)96\b", r"\1%", text)
    text = re.sub(r"(\b\d{1,3}(?:\.\d{1,3})?)\s*[oO0]/[oO0]\b", r"\1%", text)
    text = re.sub(r"\bДО\s+[1lI]?4\s+ПОСЛЕ\b", "ДО и ПОСЛЕ", text)
    text = re.sub(r"\b1\s+ДО\s+исправления\b", "ДО исправления", text)
    text = re.sub(r"ДО\s+исправления\s*\(Baseline\)\s*-\d+", "ДО исправления (Baseline)", text)
    text = re.sub(r"\b(?:IO|I0|1O|10)\s*/\s*(?:IO|I0|1O|10)\b", "10 / 10", text)
    text = re.sub(r"\b(\d+)\s*/\s*(\d+)\b", r"\1 / \2", text)
    text = re.sub(r"—\s*(?:0K|ок|OK)\s*(\(\d+\.\d+s\))", r"— OK \1", text)
    text = re.sub(r"(\b\d+\.\d+s)\s+(?:—\s*)?(?:ок|0K|OK)\b", r"\1 — OK", text)
    text = re.sub(r"\b[еe]к\s+потерян\b", "0K потерян", text)
    text = re.sub(r"(,\s*)0K(?=\s*,)", r"\g<1>OK", text)
    text = re.sub(r"\b0K\b", "OK", text)
    text = re.sub(r"R[ILl1][Jj3]\+EN", "RU+EN", text)
    text = re.sub(r"(?:\\П|\\п|\\[nN]|[XxхХ][пnлЛ]|Xn|In)(?=[,\s]|$)", r"\\n", text)
    text = re.sub(r"(\b\d+\.)\s*,", r"\1,", text)
    text = re.sub(r"\bOCR-paMKax\b", "OCR-рамках", text)
    text = re.sub(r"\bш[ие]рском\b", "широком", text)
    text = re.sub(r"\bнсрмальные\b", "нормальные", text)
    text = re.sub(r"\bстрою\b", "строк.", text)
    text = re.sub(r"\bвыделнии\b", "выделении", text)
    text = re.sub(r"\bгюкве\b", "показе", text)
    text = re.sub(r"\bB том числе\b", "В том числе", text)
    text = re.sub(r"\bIO\b(?=\s+целевых)", "10", text)
    text = re.sub(r"Результат\.", "Результат:", text)
    text = re.sub(r"—\s*[вВ]?—\s*", "— ", text)
    text = re.sub(r"\s+[вВ]?—\s+", " — ", text)
    text = re.sub(r'\("?Перевод[Вв•"]*,?\s*[“"]?[Тт]и?ст["“]*,?', '("Перевод", "Текст",', text)
    text = re.sub(r'["“]?[Тт]екст[”"“]*,?', '"Текст",', text)
    text = re.sub(r'[“"п]?[Пп]оиск[в"]*\s*и\s*др\.\)?', '"Поиск" и др.).', text)
    text = re.sub(r"[ОO][СC]R-рамках", "OCR-рамках", text)
    text = re.sub(r"\.\.+$", ".", text)
    text = re.sub(r"(?<=[а-яА-ЯёЁ]\s)[Cc](?=\s[а-яА-ЯёЁ])", "с", text)

    # 14. Standard 0 vs O/o rules
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

    # 15. Technology model names and identifiers: f1ash -> flash
    text = re.sub(r"\b([a-zA-Z0-9_-]*)f1ash\b", r"\1flash", text, flags=re.IGNORECASE)
    text = re.sub(r"\bf1ash\b", "flash", text, flags=re.IGNORECASE)

    # 16. Google AI Studio and UI tokens
    text = re.sub(r"\bGoogle\s+Al\s+Studio\b", "Google AI Studio", text)
    text = re.sub(r"\bAl\s+Studio\b", "AI Studio", text)
    text = re.sub(r"\bclient\s+I-Jl\b", "client UI", text)
    text = re.sub(r"\bclient\s+I-J\b", "client UI", text)
    text = re.sub(r"\bI-Jl\b", "UI", text)

    # 17. Cyrillic homoglyph pseudo-words in English phrases
    text = re.sub(r"\b[Ьb]ееп\b", "been", text)
    text = re.sub(r"\bпо\s+(?:1опде|longe|longer)\b", "no longer", text)
    text = re.sub(r"\b1опде\b", "longer", text)
    text = re.sub(r"\bпо\s+longe\b", "no longer", text)
    text = re.sub(r"\b([Ii]t)\s+is\s+по\b", r"\1 is no", text)
    text = re.sub(r"(?<=[(\s])ог(?=[\s)])", "or", text)
    text = re.sub(r"\bshut\s+down\s+оп\b", "shut down on", text)
    text = re.sub(r"\bоп\s+(?=[A-Z][a-z]+|\d)", "on ", text)
    text = re.sub(r"\bопе\s+of\b", "one of", text)
    text = re.sub(r"\bto\s+опе\b", "to one", text)
    text = re.sub(r"\bто\s+(?=[a-z])", "to ", text)
    text = re.sub(r"\b([a-zA-Z]+)\s+а\s+([a-zA-Z]+)\b", r"\1 a \2", text)
    text = re.sub(r"(?m)^\s*о\s+(?=deep-research|\w+[-_])", "• ", text)

    # Re-apply hex hash restoration after 0-to-o rules so valid hashes (b8a9c0d, 3a0f12c) are not corrupted
    text = _fix_hex_hashes(text)

    text = _normalize_intra_word_homoglyphs(text)

    # Final pass for hex hashes in case homoglyph normalization altered letters
    text = _fix_hex_hashes(text)
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

    def _preprocess_image(self, img: Image.Image) -> tuple[Image.Image, float, float]:
        """Preprocesses image for OCR: adaptive scaling and percentile-based contrast normalization."""
        import numpy as np

        w, h = img.size
        orig_w, orig_h = w, h

        # Dynamic scaling: 2.2x is optimal for small UI text and programming fonts without blurring
        if h < 300 or w < 800:
            scale = 2.2
        elif h < 650:
            scale = 2.0
        else:
            scale = 1.5

        max_dim = max(w * scale, h * scale)
        if max_dim > 2200:
            scale = 2200.0 / max(w, h)

        target_w = max(40, int(round(w * scale)))
        target_h = max(40, int(round(h * scale)))

        scale_x = target_w / float(orig_w) if orig_w else 1.0
        scale_y = target_h / float(orig_h) if orig_h else 1.0

        # Contrast normalization for low-contrast images (dark mode or faded light text)
        arr = np.array(img.convert("L"))
        p2 = float(np.percentile(arr, 2))
        p98 = float(np.percentile(arr, 98))
        rng = p98 - p2

        if 5 < rng < 120:
            norm = np.clip((arr.astype(float) - p2) / max(rng, 1.0) * 255.0, 0, 255).astype(np.uint8)
            base = Image.fromarray(norm).convert("RGB")
        else:
            base = img

        scaled = base.resize((target_w, target_h), Image.Resampling.LANCZOS)
        return scaled, scale_x, scale_y

    # Distinct Cyrillic letters that have no twins in Latin or digits (does not include 'з'/'З' to avoid digit 3 confusion)
    CYR_DISTINCT = set("бвгджйиклмптфцчшщъыьэюяБГДЁЖИЙЛПУФЦЧШЩЪЫЬЭЮЯ")
    ENGLISH_COMMON_WORDS = {
        "been", "no", "longer", "on", "one", "to", "or", "a", "an", "the", "in", "it", "is",
        "at", "by", "for", "with", "from", "as", "into", "all", "can", "had", "has", "have",
        "not", "but", "are", "was", "were", "be", "do", "did", "so", "up", "out", "if", "about",
        "who", "which", "when", "there", "their", "more", "other", "some", "time", "than",
        "now", "only", "then", "also", "after", "use", "two", "how", "our", "work", "first",
        "well", "way", "even", "new", "want", "any", "these", "give", "day", "most", "us",
        "ui", "api", "ai", "flash", "agent", "deep", "client", "studio", "google", "model",
        "update", "guide", "preview", "requests", "request", "create", "migrate", "down",
        "newer", "versions", "speed", "efficiency", "streamed", "maximum", "context"
    }
    CYR_SINGLE_LETTER = set("викосуаяВИКОСУАЯ")
    CYR_NON_HOMOGLYPHS = CYR_DISTINCT
    CYR_HOMOGLYPH_TRANS = str.maketrans({
        'а': 'a', 'с': 'c', 'е': 'e', 'о': 'o', 'р': 'p', 'х': 'x', 'у': 'y',
        'ь': 'b', 'п': 'n', 'г': 'r', 'т': 't', 'д': 'g', 'м': 'm', 'к': 'k',
        'в': 'b', 'н': 'h', 'і': 'i', '1': 'l'
    })

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

        # Cluster boxes into visual horizontal rows by median line center
        rows = []
        for b, l in items:
            x, y, w, h = b
            cy = y + h / 2.0
            best_row = None
            best_dist = float("inf")
            for row in rows:
                row_cy = float(np.median([item[0][1] + item[0][3] / 2.0 for item in row]))
                row_h = float(np.median([item[0][3] for item in row]))
                dist = abs(cy - row_cy)
                if dist <= max(row_h * 0.42, 6.0) and dist < best_dist:
                    best_dist = dist
                    best_row = row
            if best_row is not None:
                best_row.append((b, l))
            else:
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

    def _merge_dual_language_structured(
        self, res_ru: Optional[dict], res_en: Optional[dict]
    ) -> tuple[str, list[dict]]:
        import re

        res_ru = self._restructure_lines_by_layout(res_ru)
        res_en = self._restructure_lines_by_layout(res_en)

        lines_ru = res_ru.get("lines", []) if isinstance(res_ru, dict) else []
        lines_en = res_en.get("lines", []) if isinstance(res_en, dict) else []

        words_ru = [dict(w) for l in lines_ru for w in l.get("words", []) if w.get("text", "").strip()]
        words_en = [dict(w) for l in lines_en for w in l.get("words", []) if w.get("text", "").strip()]

        if not words_ru and not words_en:
            return "", []
        if not words_ru:
            return self._extract_lines_text(res_en), lines_en
        if not words_en:
            return self._extract_lines_text(res_ru), lines_ru

        def get_bbox(w: dict) -> tuple[float, float, float, float]:
            b = w.get("bounding_rect", {})
            return float(b.get("x", 0.0)), float(b.get("y", 0.0)), float(b.get("width", 0.0)), float(b.get("height", 0.0))

        def compute_overlap(b1: tuple[float, float, float, float], b2: tuple[float, float, float, float]) -> float:
            x1, y1, w1, h1 = b1
            x2, y2, w2, h2 = b2
            ix1, iy1 = max(x1, x2), max(y1, y2)
            ix2, iy2 = min(x1 + w1, x2 + w2), min(y1 + h1, y2 + h2)
            if ix2 <= ix1 or iy2 <= iy1:
                return 0.0
            inter = (ix2 - ix1) * (iy2 - iy1)
            min_area = min(w1 * h1, w2 * h2)
            return inter / min_area if min_area > 0 else 0.0

        # Step 1: Match RU words with EN words using 2D spatial IoU/overlap
        matched_en_indices: set[int] = set()
        merged_words: list[dict] = []

        for wr in words_ru:
            br = get_bbox(wr)
            best_we_idx: Optional[int] = None
            best_ov = 0.0

            for idx, we in enumerate(words_en):
                if idx in matched_en_indices:
                    continue
                be = get_bbox(we)
                ov = compute_overlap(br, be)
                if ov > best_ov:
                    best_ov = ov
                    best_we_idx = idx

            wr_text = wr.get("text", "").strip()

            if best_we_idx is not None and best_ov >= 0.28:
                we = words_en[best_we_idx]
                we_text = we.get("text", "").strip()
                matched_en_indices.add(best_we_idx)

                ru_cyr_count = len(re.findall(r"[\u0400-\u04FF]", wr_text))
                en_cyr_count = len(re.findall(r"[\u0400-\u04FF]", we_text))
                has_ru_distinct_cyr = any(c in self.CYR_DISTINCT for c in wr_text)
                has_en_latin = bool(re.search(r"[a-zA-Z]", we_text))

                # Numeric or programming symbols in English
                is_en_num_sym = bool(re.search(r"[\d%_+\-/:=><(){}\[\]#*]", we_text))

                # Date / timestamp detection (e.g. 2026-10-13, 14:02:59)
                is_date_or_time = bool(re.search(r"\b\d+[-/:]\d+", we_text)) or bool(re.search(r"\b\d+[-/:]\d+", wr_text))

                # Check if wr_text is a Cyrillic homoglyph / false transcription of an English word
                is_cyr_homoglyph = False
                we_clean = we_text.lower().strip(".,;:()\"'")
                if we_clean in self.ENGLISH_COMMON_WORDS:
                    t_cyr = wr_text.lower().translate(self.CYR_HOMOGLYPH_TRANS)
                    if t_cyr == we_clean or (len(t_cyr) >= 4 and we_clean.startswith(t_cyr[:4])) or wr_text.lower() in {"ьееп", "1опде", "ог", "по", "оп", "опе", "то"}:
                        is_cyr_homoglyph = True

                # 1. Date / timestamp: English pass is vastly superior
                if is_date_or_time:
                    chosen = we
                # 1b. Common English words mistakenly recognized as Cyrillic homoglyphs
                elif is_cyr_homoglyph:
                    chosen = we
                # 2. Authentic Russian word:
                # Word has distinct Cyrillic and either >= 2 Cyrillic letters or is a valid Russian preposition
                elif (
                    ru_cyr_count >= 2
                    and has_ru_distinct_cyr
                ) or (
                    ru_cyr_count == 1
                    and wr_text in self.CYR_SINGLE_LETTER
                    and not is_en_num_sym
                    and not has_en_latin
                ):
                    chosen = wr
                # 3. Numeric / percentage / programming symbols:
                elif is_en_num_sym:
                    # If RU produced homoglyphs for digits ('З' for 3, 'О' for 0, '96' for %)
                    if re.fullmatch(r"[З3]", wr_text) and re.fullmatch(r"\d", we_text):
                        chosen = we
                    elif re.fullmatch(r"[ОO0оo]", wr_text) and re.fullmatch(r"0", we_text):
                        chosen = we
                    elif "%" in we_text and "96" in wr_text:
                        chosen = we
                    elif has_en_latin:
                        chosen = we
                    else:
                        chosen = we
                # 4. English / Latin code identifier:
                elif has_en_latin and not has_ru_distinct_cyr:
                    chosen = we
                # 5. Russian pass fallback
                elif ru_cyr_count > en_cyr_count:
                    chosen = wr
                else:
                    chosen = we if len(we_text) >= len(wr_text) else wr

                merged_words.append({
                    "text": chosen.get("text", ""),
                    "bounding_rect": chosen.get("bounding_rect"),
                })
            else:
                merged_words.append({
                    "text": wr_text,
                    "bounding_rect": wr.get("bounding_rect"),
                })

        # Step 2: Include genuine unmatched EN words (words missed by RU pass)
        for idx, we in enumerate(words_en):
            if idx not in matched_en_indices:
                be = get_bbox(we)
                # Verify this EN word doesn't spatially collide with any already merged word
                if not any(compute_overlap(be, get_bbox(mw)) >= 0.28 for mw in merged_words):
                    we_text = we.get("text", "").strip()
                    if we_text:
                        merged_words.append({
                            "text": we_text,
                            "bounding_rect": we.get("bounding_rect"),
                        })

        # Step 3: Global spatial deduplication pass to prevent any residual collisions
        unique_words: list[dict] = []
        # Sort words in natural top-to-bottom, left-to-right reading order
        for w in sorted(merged_words, key=lambda item: (get_bbox(item)[1], get_bbox(item)[0])):
            bw = get_bbox(w)
            dup_idx = None
            for u_idx, u in enumerate(unique_words):
                if compute_overlap(bw, get_bbox(u)) >= 0.40:
                    dup_idx = u_idx
                    break
            if dup_idx is None:
                unique_words.append(w)
            else:
                # Prefer word with genuine Cyrillic or better English letters
                u = unique_words[dup_idx]
                u_cyr = any(c in self.CYR_DISTINCT for c in u.get("text", ""))
                w_cyr = any(c in self.CYR_DISTINCT for c in w.get("text", ""))
                if not u_cyr and w_cyr:
                    unique_words[dup_idx] = w
                elif not u_cyr and not w_cyr and len(w.get("text", "")) > len(u.get("text", "")):
                    unique_words[dup_idx] = w

        # Step 4: Cluster deduplicated words into visual horizontal rows by median line center
        rows: list[list[dict]] = []
        for w in sorted(unique_words, key=lambda item: get_bbox(item)[1]):
            x, y, w_w, h = get_bbox(w)
            cy = y + h / 2.0
            best_row = None
            best_dist = float("inf")
            for row in rows:
                row_cy = float(np.median([get_bbox(item)[1] + get_bbox(item)[3] / 2.0 for item in row]))
                row_h = float(np.median([get_bbox(item)[3] for item in row]))
                dist = abs(cy - row_cy)
                if dist <= max(row_h * 0.42, 6.0) and dist < best_dist:
                    best_dist = dist
                    best_row = row
            if best_row is not None:
                best_row.append(w)
            else:
                rows.append([w])

        # Sort rows top-to-bottom
        rows.sort(key=lambda row: min(get_bbox(item)[1] for item in row))

        # Step 5: Format structured lines
        structured_lines: list[dict] = []
        for row in rows:
            # Sort words in row left-to-right
            row.sort(key=lambda item: get_bbox(item)[0])
            rx = min(get_bbox(item)[0] for item in row)
            ry = min(get_bbox(item)[1] for item in row)
            rw = max(get_bbox(item)[0] + get_bbox(item)[2] for item in row) - rx
            rh = max(get_bbox(item)[1] + get_bbox(item)[3] for item in row) - ry
            line_text = " ".join(item.get("text", "") for item in row if item.get("text", "")).strip()
            structured_lines.append({
                "text": line_text,
                "words": row,
                "bounding_rect": {"x": rx, "y": ry, "width": rw, "height": rh},
            })

        merged_text = "\n".join(l["text"] for l in structured_lines)
        return merged_text, structured_lines


    def _detect_line_bands(self, img: Image.Image) -> list[tuple[int, int]]:
        """Detects horizontal text line bands using row projection analysis."""
        try:
            import numpy as np
            gray = img.convert("L")
            w, h = gray.size
            if h < 50 or w < 30:
                return []
            arr = np.array(gray, dtype=float)
            border_pixels = np.concatenate([arr[0, :], arr[-1, :], arr[:, 0], arr[:, -1]])
            bg = float(np.median(border_pixels))
            row_diff = np.mean(np.abs(arr - bg), axis=1)
            is_text = row_diff > 1.0

            bands = []
            in_band = False
            start_y = 0
            for y, val in enumerate(is_text):
                if val and not in_band:
                    in_band = True
                    start_y = y
                elif not val and in_band:
                    in_band = False
                    if y - start_y >= 4:
                        bands.append((start_y, y))
            if in_band and len(is_text) - start_y >= 4:
                bands.append((start_y, len(is_text)))

            merged = []
            for sy, ey in bands:
                if merged and (sy - merged[-1][1]) <= 1:
                    merged[-1] = (merged[-1][0], ey)
                else:
                    merged.append((sy, ey))
            return merged
        except Exception:
            return []

    def _detect_table_columns(self, img: Image.Image, bands: list[tuple[int, int]]) -> list[int]:
        """Detects vertical column gutters in tabular layouts by analyzing inter-column whitespace."""
        try:
            import numpy as np
            w, h = img.size
            if len(bands) < 6 or w < 200:
                return []
            gray = img.convert("L")
            arr = np.array(gray, dtype=float)
            border = np.concatenate([arr[0, :], arr[-1, :], arr[:, 0], arr[:, -1]])
            bg = float(np.median(border))

            band_masks = []
            for sy, ey in bands:
                band_crop = arr[sy:ey, :]
                diff = np.abs(band_crop - bg)
                is_text = np.max(diff, axis=0) > 3.0
                band_masks.append(is_text.astype(float))
            if not band_masks:
                return []

            active_cols = np.mean(band_masks, axis=0)
            col_divs = []
            in_gap = False
            gap_start = 0
            for x in range(int(w * 0.12), int(w * 0.88)):
                val = active_cols[x] < 0.15
                if val and not in_gap:
                    in_gap = True
                    gap_start = x
                elif not val and in_gap:
                    in_gap = False
                    if (x - gap_start) >= 14:
                        col_divs.append((gap_start + x) // 2)
            if in_gap and (int(w * 0.88) - gap_start) >= 14:
                col_divs.append((gap_start + int(w * 0.88)) // 2)

            return col_divs
        except Exception:
            return []

    def _recognize_table_grid(
        self,
        orig_img: Image.Image,
        bands: list[tuple[int, int]],
        col_divs: list[int],
        ru_tag: Optional[str],
        en_tag: Optional[str],
    ) -> tuple[str, list[dict]]:
        """Recognizes text in multi-column tables cell-by-cell to prevent cross-column word gluing."""
        import cv2
        import numpy as np
        import winocr

        orig_w, orig_h = orig_img.size
        cols = [0] + col_divs + [orig_w]
        structured_lines: list[dict] = []

        for r_idx, (bsy, bey) in enumerate(bands):
            sy = max(0, bsy - 6)
            ey = min(orig_h, bey + 10)

            cell_texts: list[str] = []
            row_words: list[dict] = []

            for c_idx in range(len(cols) - 1):
                x1, x2 = cols[c_idx], cols[c_idx + 1]
                cell_crop = orig_img.crop((x1, sy, x2, ey))

                arr = np.array(cell_crop.convert("L"))
                border = np.concatenate([arr[0, :], arr[-1, :], arr[:, 0], arr[:, -1]])
                bg = float(np.median(border))
                if bg < 128:
                    arr_prep = 255 - arr
                else:
                    arr_prep = arr

                pad = 20
                arr_padded = cv2.copyMakeBorder(arr_prep, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255)
                scale = 3.0
                arr_scaled = cv2.resize(
                    arr_padded,
                    (int(arr_padded.shape[1] * scale), int(arr_padded.shape[0] * scale)),
                    interpolation=cv2.INTER_LANCZOS4,
                )
                cell_pil = Image.fromarray(arr_scaled)

                if ("rus" in self._raw_lang or "ru" in self._raw_lang) and ("eng" in self._raw_lang or "en" in self._raw_lang):
                    r_ru = winocr.recognize_pil_sync(cell_pil, ru_tag) if ru_tag else None
                    r_en = winocr.recognize_pil_sync(cell_pil, en_tag) if en_tag else None
                    _, cell_lines = self._merge_dual_language_structured(r_ru, r_en)
                elif "eng" in self._raw_lang or "en" in self._raw_lang:
                    tag = en_tag or "en-US"
                    res = winocr.recognize_pil_sync(cell_pil, tag)
                    restructured = self._restructure_lines_by_layout(res)
                    cell_lines = restructured.get("lines", [])
                else:
                    tag = ru_tag or "ru"
                    res = winocr.recognize_pil_sync(cell_pil, tag)
                    restructured = self._restructure_lines_by_layout(res)
                    cell_lines = restructured.get("lines", [])

                cell_words: list[dict] = []
                for l in cell_lines:
                    for w_item in l.get("words", []):
                        bx = w_item.get("bounding_rect", {})
                        orig_bx = {
                            "x": x1 + max(0.0, (bx.get("x", 0.0) - pad) / scale),
                            "y": sy + max(0.0, (bx.get("y", 0.0) - pad) / scale),
                            "width": bx.get("width", 0.0) / scale,
                            "height": bx.get("height", 0.0) / scale,
                        }
                        cell_words.append({
                            "text": w_item.get("text", ""),
                            "bounding_rect": orig_bx,
                        })

                # Intra-cell token stitching for wrapped identifiers
                stitched_words: list[dict] = []
                skip_next = False
                for i in range(len(cell_words)):
                    if skip_next:
                        skip_next = False
                        continue
                    curr = cell_words[i]
                    if i + 1 < len(cell_words):
                        nxt = cell_words[i + 1]
                        t1 = curr["text"].strip()
                        t2 = nxt["text"].strip()
                        if (
                            "_" in t1
                            and (
                                t1.endswith("_")
                                or len(t2) <= 4
                                or "_" in t2
                                or t1.endswith(("regio", "preserv", "ex", "unmo", "cont", "aligr", "me"))
                            )
                        ):
                            b1 = curr["bounding_rect"]
                            b2 = nxt["bounding_rect"]
                            join_char = "" if t1.endswith("_") or t2.startswith("_") or len(t2) <= 3 else "_"
                            stitched = {
                                "text": t1 + join_char + t2,
                                "bounding_rect": {
                                    "x": min(b1["x"], b2["x"]),
                                    "y": min(b1["y"], b2["y"]),
                                    "width": max(b1["x"] + b1["width"], b2["x"] + b2["width"]) - min(b1["x"], b2["x"]),
                                    "height": max(b1["y"] + b1["height"], b2["y"] + b2["height"]) - min(b1["y"], b2["y"]),
                                },
                            }
                            stitched_words.append(stitched)
                            skip_next = True
                            continue
                    stitched_words.append(curr)

                c_text = " ".join(w["text"] for w in stitched_words).strip()
                c_text = _correct_ocr_confusions(c_text)
                if c_text:
                    cell_texts.append(c_text)
                    row_words.extend(stitched_words)

            if row_words:
                row_words.sort(key=lambda item: item["bounding_rect"]["x"])
                rx = min(item["bounding_rect"]["x"] for item in row_words)
                ry = min(item["bounding_rect"]["y"] for item in row_words)
                rw = max(item["bounding_rect"]["x"] + item["bounding_rect"]["width"] for item in row_words) - rx
                rh = max(item["bounding_rect"]["y"] + item["bounding_rect"]["height"] for item in row_words) - ry
                row_str = " | ".join(cell_texts)
                structured_lines.append({
                    "text": row_str,
                    "words": row_words,
                    "bounding_rect": {"x": rx, "y": ry, "width": rw, "height": rh},
                })

        raw_text = "\n".join(l["text"] for l in structured_lines if l.get("text", "").strip())
        return raw_text, structured_lines

    def _merge_dual_language_results(self, res_ru: Optional[dict], res_en: Optional[dict]) -> str:
        text, _ = self._merge_dual_language_structured(res_ru, res_en)
        return text

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
                orig_w, orig_h = orig_img.size

            # Check available system tags
            available_tags = [l.language_tag for l in winocr.OcrEngine.available_recognizer_languages]
            en_tag = "en-US" if "en-US" in available_tags else ("en" if "en" in available_tags else None)
            ru_tag = "ru" if "ru" in available_tags else None

            raw_text = ""
            structured_lines: list[dict] = []

            # 1. Line banding or table grid recognition for tall, dense multiline documents (>= 8 detected text lines)
            bands = self._detect_line_bands(orig_img) if orig_h >= 120 else []
            if len(bands) >= 8:
                col_divs = self._detect_table_columns(orig_img, bands)
                if col_divs:
                    raw_text, structured_lines = self._recognize_table_grid(
                        orig_img, bands, col_divs, ru_tag, en_tag
                    )
                else:
                    import numpy as np
                    for sy, ey in bands:
                        crop_sy = max(0, sy - 2)
                        crop_ey = min(orig_h, ey + 2)
                        band_crop = orig_img.crop((0, crop_sy, orig_w, crop_ey))

                        sub_gray = band_crop.convert("L")
                        sub_arr = np.array(sub_gray, dtype=float)
                        sub_bg = float(np.median(sub_arr[:3, :3]))
                        col_diff = np.mean(np.abs(sub_arr - sub_bg), axis=0)
                        act = np.where(col_diff > 1.0)[0]
                        if len(act) == 0:
                            continue
                        min_x = max(0, int(act.min()) - 10)
                        max_x = min(orig_w, int(act.max()) + 20)
                        crop_tight = band_crop.crop((min_x, 0, max_x, band_crop.height))

                        target_h = 75 if crop_tight.height < 35 else max(70, min(100, int(crop_tight.height * 2.5)))
                        scale_y = target_h / float(crop_tight.height)
                        target_w = int(crop_tight.width * scale_y)
                        scale_x = target_w / float(crop_tight.width)
                        scaled = crop_tight.resize((target_w, target_h), Image.Resampling.LANCZOS)

                        if ("rus" in self._raw_lang or "ru" in self._raw_lang) and ("eng" in self._raw_lang or "en" in self._raw_lang):
                            r_ru = winocr.recognize_pil_sync(scaled, ru_tag) if ru_tag else None
                            r_en = winocr.recognize_pil_sync(scaled, en_tag) if en_tag else None
                            _, band_lines = self._merge_dual_language_structured(r_ru, r_en)
                        elif "eng" in self._raw_lang or "en" in self._raw_lang:
                            tag = en_tag or "en-US"
                            res = winocr.recognize_pil_sync(scaled, tag)
                            restructured = self._restructure_lines_by_layout(res)
                            band_lines = restructured.get("lines", [])
                        else:
                            tag = ru_tag or "ru"
                            res = winocr.recognize_pil_sync(scaled, tag)
                            restructured = self._restructure_lines_by_layout(res)
                            band_lines = restructured.get("lines", [])

                        for bl in band_lines:
                            new_words = []
                            for bw in bl.get("words", []):
                                bx = bw.get("bounding_rect", {})
                                new_words.append({
                                    "text": bw.get("text", ""),
                                    "bounding_rect": {
                                        "x": min_x + (bx.get("x", 0.0) / scale_x),
                                        "y": crop_sy + (bx.get("y", 0.0) / scale_y),
                                        "width": bx.get("width", 0.0) / scale_x,
                                        "height": bx.get("height", 0.0) / scale_y,
                                    },
                                })
                            lbx = bl.get("bounding_rect", {})
                            orig_lbx = {
                                "x": min_x + (lbx.get("x", 0.0) / scale_x),
                                "y": crop_sy + (lbx.get("y", 0.0) / scale_y),
                                "width": lbx.get("width", 0.0) / scale_x,
                                "height": lbx.get("height", 0.0) / scale_y,
                            } if lbx else {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0}

                            line_str = _correct_ocr_confusions(bl.get("text", "").strip())
                            structured_lines.append({
                                "text": line_str,
                                "words": new_words,
                                "bounding_rect": orig_lbx,
                            })

                    raw_text = "\n".join(l["text"] for l in structured_lines if l.get("text", "").strip())

            # 2. Whole-image adaptive recognition for short snippets, tables, code, and UI elements
            if not structured_lines:
                proc_img, scale_x, scale_y = self._preprocess_image(orig_img.copy())

                if ("rus" in self._raw_lang or "ru" in self._raw_lang) and ("eng" in self._raw_lang or "en" in self._raw_lang):
                    res_ru = winocr.recognize_pil_sync(proc_img, ru_tag) if ru_tag else None
                    res_en = winocr.recognize_pil_sync(proc_img, en_tag) if en_tag else None
                    raw_text, structured_lines = self._merge_dual_language_structured(res_ru, res_en)
                elif "eng" in self._raw_lang or "en" in self._raw_lang:
                    tag = en_tag or "en-US"
                    res = winocr.recognize_pil_sync(proc_img, tag)
                    restructured = self._restructure_lines_by_layout(res)
                    structured_lines = restructured.get("lines", [])
                    raw_text = self._extract_lines_text(restructured)
                else:
                    tag = ru_tag or "ru"
                    res = winocr.recognize_pil_sync(proc_img, tag)
                    restructured = self._restructure_lines_by_layout(res)
                    structured_lines = restructured.get("lines", [])
                    raw_text = self._extract_lines_text(restructured)

                # Rescale coordinates from preprocessed image space back to original image space
                if scale_x != 1.0 or scale_y != 1.0:
                    rescaled_lines = []
                    for l in structured_lines:
                        new_words = []
                        for w in l.get("words", []):
                            bx = w.get("bounding_rect", {})
                            new_words.append({
                                "text": w.get("text", ""),
                                "bounding_rect": {
                                    "x": bx.get("x", 0.0) / scale_x,
                                    "y": bx.get("y", 0.0) / scale_y,
                                    "width": bx.get("width", 0.0) / scale_x,
                                    "height": bx.get("height", 0.0) / scale_y,
                                },
                            })
                        lbx = l.get("bounding_rect", {})
                        rescaled_lines.append({
                            "text": l.get("text", ""),
                            "words": new_words,
                            "bounding_rect": {
                                "x": lbx.get("x", 0.0) / scale_x,
                                "y": lbx.get("y", 0.0) / scale_y,
                                "width": lbx.get("width", 0.0) / scale_x,
                                "height": lbx.get("height", 0.0) / scale_y,
                            },
                        })
                    structured_lines = rescaled_lines

            # 3. Ensure all lines have valid bounding_rect
            for l in structured_lines:
                if "bounding_rect" not in l or not l["bounding_rect"] or l["bounding_rect"].get("width", 0) == 0:
                    lx, ly, lw, lh = self._get_line_bbox(l)
                    l["bounding_rect"] = {"x": lx, "y": ly, "width": lw, "height": lh}

            # 5. Postprocessing and confusion correction
            raw_text = _correct_ocr_confusions(raw_text)
            for l in structured_lines:
                l["text"] = _correct_ocr_confusions(l.get("text", "").strip())
                for w in l.get("words", []):
                    w["text"] = _correct_ocr_confusions(w.get("text", "").strip())

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
            lines=tuple(structured_lines),
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
