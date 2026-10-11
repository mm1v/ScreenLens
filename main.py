import argparse
import logging
import os
import sys
import time
from typing import Optional

# Ensure UTF-8 output on Windows console
if sys.platform == "win32":
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from core import __version__
from core.actions import open_in_google_lens, translate_text
from core.capture import ScreenAreaSelector
from core.clipboard import ClipboardManager
from core.gemini import GeminiVisionClient
from core.hotkey import HotkeyListener
from core.models import CaptureArea
from core.ocr import OCRError, TesseractNotFoundError, create_ocr_engine
from core.qr import QRCodeScanner
from core.screenshot import ScreenGrabber
from utils.logger import setup_logger


def play_success_beep() -> None:
    """Plays an unobtrusive Windows audio notification upon successful OCR/copy."""
    if sys.platform == "win32":
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass


def parse_arguments() -> argparse.Namespace:
    """Parses command-line arguments."""
    parser = argparse.ArgumentParser(
        description=f"ScreenLens {__version__} — умный захват области экрана, OCR, QR-сканер, перевод и Gemini Vision."
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Запустить в фоновом режиме ожидания глобального хоткея.",
    )
    parser.add_argument(
        "--hotkey",
        type=str,
        default="<ctrl>+<alt>+space",
        help="Комбинация глобального хоткея (по умолчанию: '<ctrl>+<alt>+space').",
    )
    parser.add_argument(
        "--no-copy",
        action="store_true",
        help="Отключить автоматическое копирование результата в буфер обмена.",
    )
    parser.add_argument(
        "--engine",
        type=str,
        choices=["auto", "tesseract", "winocr"],
        default="auto",
        help="Движок OCR: 'auto' (Tesseract -> Windows OCR), 'tesseract', или 'winocr'.",
    )
    parser.add_argument(
        "--lang",
        type=str,
        default="rus+eng",
        help="Языки для OCR (по умолчанию: rus+eng).",
    )
    parser.add_argument(
        "--translate",
        nargs="?",
        const="ru",
        default=None,
        metavar="LANG",
        help="Автоматический перевод распознанного текста (по умолчанию: 'ru').",
    )
    parser.add_argument(
        "--gemini",
        action="store_true",
        help="Включить мультимодальный ИИ-анализ области через Google Gemini 3.5 Flash Lite.",
    )
    parser.add_argument(
        "--gemini-prompt",
        type=str,
        default=None,
        help="Пользовательский запрос для Gemini Vision.",
    )
    parser.add_argument(
        "--gemini-key",
        type=str,
        default=None,
        help="Google AI Studio API ключ (по умолчанию берётся из переменной GEMINI_API_KEY).",
    )
    parser.add_argument(
        "--no-dns-ai",
        action="store_true",
        help="Отключить автоматический обход гео-блокировки Gemini через DNS-AI.",
    )
    parser.add_argument(
        "--lens",
        action="store_true",
        help="Открыть визуальный поиск Google Lens в браузере.",
    )
    parser.add_argument(
        "--no-qr",
        action="store_true",
        help="Отключить сканирование QR-кодов.",
    )
    parser.add_argument(
        "--tesseract-path",
        type=str,
        default=None,
        help="Путь к исполняемому файлу tesseract.exe (если не найден автоматически).",
    )
    parser.add_argument(
        "--coords",
        nargs=4,
        type=int,
        metavar=("X1", "Y1", "X2", "Y2"),
        help="Ручные координаты области (x1 y1 x2 y2) без интерактивного оверлея.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Включить подробный вывод отладочных логов.",
    )
    return parser.parse_args()


def run_pipeline(
    engine: str = "auto",
    lang: str = "rus+eng",
    translate_to: Optional[str] = None,
    use_gemini: bool = False,
    gemini_prompt: Optional[str] = None,
    gemini_key: Optional[str] = None,
    open_lens: bool = False,
    check_qr: bool = True,
    tesseract_path: Optional[str] = None,
    coords: Optional[list[int]] = None,
    no_dns_ai: bool = False,
    auto_copy: bool = True,
    debug: bool = False,
    logger: Optional[logging.Logger] = None,
) -> int:
    """Main orchestration pipeline for ScreenLens 0.0.1."""
    if logger is None:
        log_level = logging.DEBUG if debug else logging.INFO
        logger = setup_logger("screenlens", level=log_level)

    print("=" * 60, flush=True)
    print(f"                ScreenLens {__version__} (Smart Pipeline)", flush=True)
    print("=" * 60, flush=True)

    # 1. Determine capture area
    area: Optional[CaptureArea] = None
    if coords:
        area = CaptureArea(x1=coords[0], y1=coords[1], x2=coords[2], y2=coords[3])
        if not area.is_valid:
            logger.error("Переданные координаты некорректны: %s", coords)
            return 1
        logger.info("Используются координаты из аргументов: bbox=%s", area.bbox)
    else:
        print("\n[ИНСТРУКЦИЯ] Выделите область на экране (зажмите ЛКМ).", flush=True)
        print("             Для отмены нажмите клавишу ESC.\n", flush=True)
        selector = ScreenAreaSelector(logger=logger)
        try:
            area = selector.select_area()
        except Exception as exc:
            logger.error("Ошибка при работе оверлея выделения: %s", exc)
            return 1

    if area is None:
        print("\n[ИНФО] Выделение области отменено пользователем.", flush=True)
        return 0

    # 2. Capture screenshot of selected area
    grabber = ScreenGrabber(logger=logger)
    try:
        shot_result = grabber.capture(area)
    except Exception as exc:
        logger.error("Не удалось сделать снимок экрана: %s", exc)
        return 1

    print(f"\n[СНИМОК] Сохранён во временный файл:", flush=True)
    print(f"         Путь: {shot_result.image_path}", flush=True)
    print(f"         Размер: {shot_result.width}x{shot_result.height} px", flush=True)

    clipboard_content = ""

    # 3. QR code scan
    if check_qr:
        qr_scanner = QRCodeScanner(logger=logger)
        qr_data = qr_scanner.scan(shot_result.image_path)
        if qr_data:
            print("\n" + "#" * 60, flush=True)
            print("                 ОБНАРУЖЕН QR-КОД", flush=True)
            print("#" * 60, flush=True)
            print(qr_data, flush=True)
            print("#" * 60, flush=True)
            clipboard_content = qr_data

    # 4. OCR
    print("\n[OCR] Распознавание текста...", flush=True)
    try:
        ocr_engine = create_ocr_engine(
            engine_name=engine,
            tesseract_cmd=tesseract_path,
            language=lang,
            logger=logger,
        )
        result = ocr_engine.recognize(shot_result.image_path)
    except TesseractNotFoundError as exc:
        print("\n" + "!" * 60, flush=True)
        print("[ОШИБКА OCR] Tesseract не настроен:", flush=True)
        print(str(exc), flush=True)
        print("!" * 60, flush=True)
        return 2
    except OCRError as exc:
        logger.error("Сбой во время OCR: %s", exc)
        return 3
    except Exception as exc:
        logger.exception("Непредвиденная ошибка OCR: %s", exc)
        return 4

    print("\n" + "-" * 60, flush=True)
    print("                  РАСПОЗНАННЫЙ ТЕКСТ", flush=True)
    print("-" * 60, flush=True)

    if result.is_empty:
        print("[ИНФО] Текст на изображении не обнаружен.", flush=True)
    else:
        print(result.cleaned_text, flush=True)
        if not clipboard_content:
            clipboard_content = result.cleaned_text

    print("-" * 60, flush=True)

    # 5. Translation
    if translate_to and not result.is_empty:
        print(f"\n[ПЕРЕВОД -> {translate_to.upper()}]:", flush=True)
        translated = translate_text(result.cleaned_text, target_lang=translate_to, logger=logger)
        print(translated, flush=True)
        print("-" * 60, flush=True)

    # 6. Gemini Vision analysis
    if use_gemini or gemini_prompt:
        print("\n[ИИ-АНАЛИЗ] Запрос к Google Gemini 3.5 Flash Lite...", flush=True)
        gemini_client = GeminiVisionClient(
            api_key=gemini_key,
            use_dns_ai=not no_dns_ai,
            logger=logger,
        )
        prompt = gemini_prompt or (
            "Опиши подробно, что изображено на этом снимке экрана. "
            "Если здесь ошибка, код или вопрос — объясни суть и предложи решение."
        )
        try:
            ai_response = gemini_client.analyze_image(shot_result.image_path, prompt=prompt)
            print("\n" + "*" * 60, flush=True)
            print("         ОТВЕТ GEMINI 3.5 FLASH LITE", flush=True)
            print("*" * 60, flush=True)
            print(ai_response, flush=True)
            print("*" * 60, flush=True)
        except Exception as exc:
            print(f"[ОШИБКА GEMINI]: {exc}", flush=True)

    # 7. Reverse Visual Search
    if open_lens:
        print("\n[ПОИСК] Открытие Google Lens в браузере...", flush=True)
        open_in_google_lens()

    # 8. Copy to clipboard
    if auto_copy and clipboard_content:
        clipboard = ClipboardManager(logger=logger)
        if clipboard.copy(clipboard_content):
            print("\n[БУФЕР] Результат скопирован в буфер обмена!", flush=True)
            play_success_beep()

    print("", flush=True)
    return 0


def run_daemon_mode(
    hotkey_combo: str,
    engine: str,
    lang: str,
    translate_to: Optional[str],
    use_gemini: bool,
    gemini_prompt: Optional[str],
    gemini_key: Optional[str],
    open_lens: bool,
    check_qr: bool,
    tesseract_path: Optional[str],
    no_dns_ai: bool,
    auto_copy: bool,
    debug: bool,
) -> None:
    """Runs resident daemon listening to global hotkey on the main thread."""
    log_level = logging.DEBUG if debug else logging.INFO
    logger = setup_logger("screenlens", level=log_level)

    print("=" * 60, flush=True)
    print(f"           ScreenLens {__version__} — Фоновый режим (Daemon)", flush=True)
    print("=" * 60, flush=True)
    print(f"\n[АКТИВЕН] Хоткей: {hotkey_combo}", flush=True)
    print("          Нажмите эту комбинацию в ЛЮБОЙ программе для захвата экрана.", flush=True)
    print("          (Работает при любой раскладке клавиатуры: RU и EN).", flush=True)
    if translate_to:
        print(f"          Автоперевод: включён (-> {translate_to.upper()}).", flush=True)
    if use_gemini:
        print("          Gemini Vision: включён (Gemini 3.5 Flash Lite).", flush=True)
    print("          Для остановки нажмите Ctrl+C в этой консоли.\n", flush=True)

    def on_hotkey_pressed() -> None:
        run_pipeline(
            engine=engine,
            lang=lang,
            translate_to=translate_to,
            use_gemini=use_gemini,
            gemini_prompt=gemini_prompt,
            gemini_key=gemini_key,
            open_lens=open_lens,
            check_qr=check_qr,
            tesseract_path=tesseract_path,
            no_dns_ai=no_dns_ai,
            auto_copy=auto_copy,
            debug=debug,
            logger=logger,
        )

    listener = HotkeyListener(
        hotkey_combo=hotkey_combo,
        on_trigger=on_hotkey_pressed,
        logger=logger,
    )

    try:
        listener.listen_loop()
    except KeyboardInterrupt:
        print("\n[ИНФО] Остановка фонового режима по сигналу пользователя...", flush=True)
    finally:
        listener.stop()
        print("[ИНФО] Фоновый режим остановлен.", flush=True)


def main() -> None:
    """CLI entrypoint."""
    args = parse_arguments()
    auto_copy = not args.no_copy
    check_qr = not args.no_qr

    if args.daemon:
        run_daemon_mode(
            hotkey_combo=args.hotkey,
            engine=args.engine,
            lang=args.lang,
            translate_to=args.translate,
            use_gemini=args.gemini,
            gemini_prompt=args.gemini_prompt,
            gemini_key=args.gemini_key,
            open_lens=args.lens,
            check_qr=check_qr,
            tesseract_path=args.tesseract_path,
            no_dns_ai=args.no_dns_ai,
            auto_copy=auto_copy,
            debug=args.debug,
        )
        sys.exit(0)

    exit_code = run_pipeline(
        engine=args.engine,
        lang=args.lang,
        translate_to=args.translate,
        use_gemini=args.gemini,
        gemini_prompt=args.gemini_prompt,
        gemini_key=args.gemini_key,
        open_lens=args.lens,
        check_qr=check_qr,
        tesseract_path=args.tesseract_path,
        coords=args.coords,
        no_dns_ai=args.no_dns_ai,
        auto_copy=auto_copy,
        debug=args.debug,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
