import ctypes
import logging
import queue
import sys
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from typing import Any, Optional

from PIL import ImageEnhance, ImageGrab, ImageTk
from core.clipboard import copy_image_to_clipboard, get_clipboard_text, setup_universal_clipboard_shortcuts
from core.models import CaptureArea, ScreenshotResult
from core.actions import open_in_google_lens, translate_text
from core.gemini import GeminiVisionClient
from core.ocr import create_ocr_engine
from ui.text_selection_layer import SelectableTextLayer
from ui.floating_hud import (
    CapsuleButton,
    GoogleLensHUD,
    RoundedCard,
    RoundedCardController,
    calc_pill_width,
    create_rounded_card,
    get_card_placement,
    get_hud_placement,
    render_lens_corners,
    render_pill_badge,
)


def enable_windows_dpi_awareness() -> None:
    """Enables per-monitor DPI awareness for high-resolution displays on Windows."""
    if sys.platform != "win32":
        return
    try:
        # 2 = PROCESS_PER_MONITOR_DPI_AWARE
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _ensure_desktop_access() -> None:
    """Ensures current thread is attached to default interactive desktop on Windows."""
    if sys.platform != "win32":
        return
    try:
        user32 = ctypes.windll.user32
        h_default = user32.OpenDesktopW("default", 0, False, 0x01FF)
        if h_default:
            user32.SetThreadDesktop(h_default)
    except Exception:
        pass


HANDLE_NW = "nw"
HANDLE_NE = "ne"
HANDLE_SE = "se"
HANDLE_SW = "sw"

CURSOR_MAP = {
    HANDLE_NW: "size_nw_se",
    HANDLE_SE: "size_nw_se",
    HANDLE_NE: "size_ne_sw",
    HANDLE_SW: "size_ne_sw",
    "move": "fleur",
    "none": "cross",
}


def get_corner_positions(x1: int, y1: int, x2: int, y2: int) -> dict[str, tuple[int, int]]:
    """Returns exact coordinate points for the 4 corners."""
    return {
        HANDLE_NW: (x1, y1),
        HANDLE_NE: (x2, y1),
        HANDLE_SE: (x2, y2),
        HANDLE_SW: (x1, y2),
    }


def hit_test_lens(
    mx: int,
    my: int,
    box: tuple[int, int, int, int],
    corner_hitbox: int = 28,
) -> str:
    """Hit-tests mouse strictly against 4 corners or inside movement. No side markers."""
    x1, y1, x2, y2 = box
    corners = get_corner_positions(x1, y1, x2, y2)

    # 1. 4 Corner hitboxes (generous 28px radius for touch/mouse)
    for name, (cx, cy) in corners.items():
        if (mx - cx) ** 2 + (my - cy) ** 2 <= corner_hitbox ** 2:
            return name

    # 2. Inside area
    if x1 + 10 < mx < x2 - 10 and y1 + 10 < my < y2 - 10:
        return "move"

    return "none"


def apply_drag_lens(
    handle: str,
    orig_box: tuple[int, int, int, int],
    dx: int,
    dy: int,
    min_w: int = 24,
    min_h: int = 24,
    max_w: int = 1920,
    max_h: int = 1080,
) -> tuple[int, int, int, int]:
    """Calculates updated bounding box after dragging one of 4 corners or moving selection."""
    x1, y1, x2, y2 = orig_box
    if handle == "move":
        w = x2 - x1
        h = y2 - y1
        nx1 = max(0, min(max_w - w, x1 + dx))
        ny1 = max(0, min(max_h - h, y1 + dy))
        return nx1, ny1, nx1 + w, ny1 + h

    nx1, ny1, nx2, ny2 = x1, y1, x2, y2
    if "w" in handle:
        nx1 = min(x2 - min_w, max(0, x1 + dx))
    if "e" in handle:
        nx2 = max(x1 + min_w, min(max_w, x2 + dx))
    if "n" in handle:
        ny1 = min(y2 - min_h, max(0, y1 + dy))
    if "s" in handle:
        ny2 = max(y1 + min_h, min(max_h, y2 + dy))
    return nx1, ny1, nx2, ny2


class ScreenAreaSelector:
    """Interactive screen overlay with Google Lens aesthetics: 4 corners only, dual capsule HUD, Material 3."""

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self._logger = logger or logging.getLogger(__name__)
        self._selected_area: Optional[CaptureArea] = None
        self._root: Optional[tk.Tk] = None
        self._canvas: Optional[tk.Canvas] = None
        self._corner_photos: dict[str, ImageTk.PhotoImage] = {}
        try:
            self._root = tk.Tk()
            self._root.withdraw()
            self._canvas = tk.Canvas(self._root, cursor="cross", bg="#141619", highlightthickness=0, bd=0)
            self._canvas.pack(fill=tk.BOTH, expand=True)
            setup_universal_clipboard_shortcuts(self._root)
        except Exception as exc:
            self._logger.warning("Could not pre-init ScreenAreaSelector Tk: %s", exc)
            self._root = None
            self._canvas = None

    @property
    def root(self) -> Optional[tk.Tk]:
        return self._root

    def close(self) -> None:
        """Destroys root window cleanly on application exit."""
        if self._root is not None:
            try:
                self._root.destroy()
            except Exception:
                pass
            self._root = None
            self._canvas = None

    def select_area(
        self,
        inline_mode: bool = False,
        settings: Optional[Any] = None,
        db: Optional[Any] = None,
        clipboard: Optional[Any] = None,
        sound_fn: Optional[Any] = None,
    ) -> Optional[CaptureArea]:
        """Launches Google Lens selection overlay, allows resizing/moving, and executes actions."""
        _ensure_desktop_access()
        enable_windows_dpi_awareness()
        self._selected_area = None

        if sys.platform == "win32":
            user32 = ctypes.windll.user32
            vx = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
            vy = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
            vw = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
            vh = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
            primary_w = user32.GetSystemMetrics(0)  # SM_CXSCREEN
            primary_h = user32.GetSystemMetrics(1)  # SM_CYSCREEN
            banner_cx = -vx + (primary_w // 2)
            banner_cy = -vy + 30
        else:
            vx, vy, vw, vh = 0, 0, 1920, 1080
            banner_cx = vw // 2
            banner_cy = 30

        full_desktop_img = None
        try:
            full_desktop_img = ImageGrab.grab(all_screens=True)
            if full_desktop_img:
                full_desktop_img = full_desktop_img.convert("RGB")
            self._logger.info("Desktop screenshot pre-captured: %s", full_desktop_img.size if full_desktop_img else None)
        except Exception as exc:
            self._logger.warning("Could not pre-capture desktop image: %s", exc)
            full_desktop_img = None

        # Pre-render anti-aliased 4-corner sprites via Pillow supersampling x4
        corner_imgs = render_lens_corners(size=44, radius=16, leg=34, thickness=3)

        if self._root is None or not self._root.winfo_exists():
            self._root = tk.Tk()
            if sys.platform == "win32":
                self._root.geometry(f"{vw}x{vh}+{vx}+{vy}")
                self._root.overrideredirect(True)
            else:
                self._root.attributes("-fullscreen", True)

            self._root.attributes("-topmost", True)
            self._canvas = tk.Canvas(self._root, cursor="cross", bg="#141619", highlightthickness=0, bd=0)
            self._canvas.pack(fill=tk.BOTH, expand=True)
            setup_universal_clipboard_shortcuts(self._root)
        else:
            try:
                if sys.platform == "win32":
                    self._root.geometry(f"{vw}x{vh}+{vx}+{vy}")
                    self._root.overrideredirect(True)
                else:
                    self._root.attributes("-fullscreen", True)
                self._root.attributes("-topmost", True)
                if self._canvas is None or not self._canvas.winfo_exists():
                    self._canvas = tk.Canvas(self._root, cursor="cross", bg="#141619", highlightthickness=0, bd=0)
                    self._canvas.pack(fill=tk.BOTH, expand=True)
            except Exception:
                pass

        root = self._root
        canvas = self._canvas

        # Store corner PhotoImages
        self._corner_photos = {k: ImageTk.PhotoImage(v) for k, v in corner_imgs.items()}

        try:
            canvas.delete("all")
        except Exception:
            pass
        root.config(cursor="cross")
        canvas.config(cursor="cross")

        if full_desktop_img:
            dimmed_img = ImageEnhance.Brightness(full_desktop_img).enhance(0.55)
            bg_photo = ImageTk.PhotoImage(dimmed_img)
            canvas.create_image(0, 0, image=bg_photo, anchor=tk.NW)
            canvas._bg_photo = bg_photo

        setup_universal_clipboard_shortcuts(root)

        is_destroyed = threading.Event()
        ui_queue: queue.Queue = queue.Queue()

        def process_ui_queue():
            if is_destroyed.is_set():
                return
            while not ui_queue.empty():
                try:
                    fn = ui_queue.get_nowait()
                    if not is_destroyed.is_set():
                        fn()
                except queue.Empty:
                    break
                except Exception as exc:
                    self._logger.exception("Ошибка при выполнении задачи в очереди UI: %s", exc)
            if not is_destroyed.is_set():
                try:
                    root.after(30, process_ui_queue)
                except Exception:
                    pass

        try:
            root.after(30, process_ui_queue)
        except Exception:
            pass

        def post_to_ui(callback):
            if not is_destroyed.is_set():
                ui_queue.put(callback)

        done_var = tk.BooleanVar(root, value=False)

        def safe_close_overlay(delay_ms: int = 0):
            if is_destroyed.is_set():
                return
            is_destroyed.set()

            def _hide():
                try:
                    root.withdraw()
                except Exception:
                    pass
                try:
                    done_var.set(True)
                except Exception:
                    pass

            if delay_ms > 0:
                try:
                    root.after(delay_ms, _hide)
                except Exception:
                    _hide()
            else:
                _hide()

        # Top instruction banner (monochrome Google style) centered on primary monitor
        canvas.create_text(
            banner_cx,
            banner_cy,
            text="Выделите область. Тяните за углы для изменения размера. ESC — отмена.",
            fill="#ffffff",
            font=("Segoe UI", 11, "bold"),
        )

        # State tracking
        current_state = "idle"
        box: Optional[tuple[int, int, int, int]] = None
        drag_handle: str = "none"
        drag_start_mx: int = 0
        drag_start_my: int = 0
        orig_box: tuple[int, int, int, int] = (0, 0, 0, 0)
        cutout_img_id: Optional[int] = None
        active_card_win: list[Optional[int]] = [None]
        active_controller: list[Optional[RoundedCardController]] = [None]
        hud_window_id: Optional[int] = None

        cached_ocr_result: list[Optional[OCRResult]] = [None]
        cached_ocr_box: list[Optional[tuple[int, int, int, int]]] = [None]
        cached_tr_result: list[Optional[str]] = [None]
        cached_tr_lang: list[Optional[str]] = [None]
        cached_tr_box: list[Optional[tuple[int, int, int, int]]] = [None]
        shot_ref: list[Optional[ScreenshotResult]] = [None]

        def show_copied_toast(msg: str = "✓ Скопировано в буфер!"):
            tx = box[0] + (box[2] - box[0]) // 2 if box else vw // 2
            ty = box[1] + (box[3] - box[1]) // 2 if box else vh // 2
            toast_photo, tw, th = render_pill_badge(msg, h=26)
            canvas._toast_photo = toast_photo
            canvas.delete("toast")
            canvas.create_image(tx - tw // 2, ty - th // 2, image=toast_photo, anchor=tk.NW, tags="toast")
            canvas.create_text(tx, ty, text=msg, fill="#ffffff", font=("Segoe UI", 9, "bold"), tags="toast")
            root.after(1200, lambda: canvas.delete("toast") if not is_destroyed.is_set() else None)

        def show_ocr_loading_indicator(loading_text: str = "Распознавание текста..."):
            canvas.delete("ocr_loading")
            if not box:
                return
            cx = (box[0] + box[2]) // 2
            cy = (box[1] + box[3]) // 2
            photo, lw, lh = render_pill_badge(loading_text, h=30)
            canvas._ocr_loading_photo = photo
            canvas.create_image(cx - lw // 2, cy - lh // 2, image=photo, anchor=tk.NW, tags="ocr_loading")
            canvas.create_text(cx, cy, text=loading_text, fill="#ffffff", font=("Segoe UI", 9, "bold"), tags="ocr_loading")

        def hide_ocr_loading_indicator():
            canvas.delete("ocr_loading")

        def handle_copy_text(val: str):
            if not val:
                return
            if clipboard:
                clipboard.copy(val)
            if sound_fn and settings and settings.sound_enabled:
                sound_fn(True)
            if db and shot_ref[0]:
                if current_state == "translate_select":
                    db.add_history(image_path=str(shot_ref[0].image_path), ocr_text="", translated_text=val)
                else:
                    db.add_history(image_path=str(shot_ref[0].image_path), ocr_text=val)
            show_copied_toast("✓ Текст скопирован!")

        text_layer = SelectableTextLayer(
            canvas=canvas,
            root=root,
            on_copy_callback=handle_copy_text,
            logger=self._logger,
        )

        def clear_active_card():
            if text_layer.is_active:
                text_layer.hide()
            canvas.delete("active_card")
            canvas.delete("ocr_loading")
            active_card_win[0] = None
            if active_controller[0]:
                active_controller[0].destroy()
                active_controller[0] = None

        def invalidate_ocr_cache():
            cached_ocr_result[0] = None
            cached_ocr_box[0] = None
            cached_tr_result[0] = None
            cached_tr_lang[0] = None
            cached_tr_box[0] = None
            if text_layer.is_active:
                text_layer.hide()

        def update_cutout_preview(bx1: int, by1: int, bx2: int, by2: int):
            nonlocal cutout_img_id
            if not full_desktop_img:
                return
            cropped = full_desktop_img.crop((bx1, by1, bx2, by2))
            cutout_photo = ImageTk.PhotoImage(cropped)
            if cutout_img_id is None:
                cutout_img_id = canvas.create_image(bx1, by1, image=cutout_photo, anchor=tk.NW)
            else:
                canvas.coords(cutout_img_id, bx1, by1)
                canvas.itemconfig(cutout_img_id, image=cutout_photo)
            canvas._cutout_photo = cutout_photo

        def draw_corners_and_badge(bx1: int, by1: int, bx2: int, by2: int):
            """Renders the 4 anti-aliased corner sprites and compact top-left dimension badge."""
            canvas.delete("lens_corners")
            canvas.delete("lens_badge")

            # 1. Place the 4 smooth anti-aliased corners (ONLY corners, no outer rectangle lines!)
            canvas.create_image(bx1, by1, image=self._corner_photos["nw"], anchor=tk.NW, tags="lens_corners")
            canvas.create_image(bx2, by1, image=self._corner_photos["ne"], anchor=tk.NE, tags="lens_corners")
            canvas.create_image(bx1, by2, image=self._corner_photos["sw"], anchor=tk.SW, tags="lens_corners")
            canvas.create_image(bx2, by2, image=self._corner_photos["se"], anchor=tk.SE, tags="lens_corners")

            # 2. Compact capsule badge pinned to top-left corner
            w = max(1, bx2 - bx1)
            h = max(1, by2 - by1)
            b_text = f"{w}×{h}"
            badge_x = bx1
            badge_y = by1 - 24 if by1 >= 28 else by1 + 18

            badge_photo, bw, bh = render_pill_badge(b_text)
            canvas._current_badge_photo = badge_photo
            canvas.create_image(badge_x, badge_y, image=badge_photo, anchor=tk.NW, tags="lens_badge")
            canvas.create_text(
                badge_x + bw // 2,
                badge_y + bh // 2,
                text=b_text,
                fill="#ffffff",
                font=("Segoe UI", 8, "bold"),
                tags="lens_badge",
            )

        def crop_and_save_current_selection() -> tuple[Path, ScreenshotResult]:
            nonlocal box
            bx1, by1, bx2, by2 = box if box else (0, 0, 100, 100)
            tf = tempfile.NamedTemporaryFile(prefix="screenlens_", suffix=".png", delete=False)
            t_path = Path(tf.name)
            tf.close()
            cache_p = Path(tempfile.gettempdir()) / "screenlens_current.png"
            cache_j = Path(tempfile.gettempdir()) / "screenlens_current.jpg"

            area = CaptureArea(x1=vx + bx1, y1=vy + by1, x2=vx + bx2, y2=vy + by2)
            self._selected_area = area

            if full_desktop_img:
                cropped = full_desktop_img.crop((bx1, by1, bx2, by2)).convert("RGB")
                cropped.save(t_path, format="PNG")
                try:
                    cropped.save(cache_p, format="PNG")
                    cropped.save(cache_j, format="JPEG", quality=95)
                except Exception:
                    pass
                shot = ScreenshotResult(image_path=t_path, area=area, width=cropped.width, height=cropped.height)
            else:
                from core.screenshot import ScreenGrabber
                grabber = ScreenGrabber(logger=self._logger)
                shot = grabber.capture(area)

            return t_path, shot

        # Action callbacks for HUD
        def action_text():
            nonlocal current_state
            # Toggle off if already in text selection mode
            if current_state == "text_select":
                text_layer.hide()
                lens_hud.reset_active_mode()
                current_state = "adjusting"
                return

            clear_active_card()
            lens_hud.set_active_mode("text")

            if box is None:
                return

            # Re-use already cached OCR result if box unchanged
            if cached_ocr_box[0] == box and cached_ocr_result[0] is not None:
                crop_pil = full_desktop_img.crop(box).convert("RGB") if full_desktop_img and box else None
                text_layer.load_from_ocr(
                    cached_ocr_result[0],
                    box,
                    crop_img=crop_pil,
                    cutout_item_id=cutout_img_id,
                    original_crop_photo=getattr(canvas, "_cutout_photo", None),
                )
                text_layer.show()
                current_state = "text_select"
                return

            t_path, shot = crop_and_save_current_selection()
            shot_ref[0] = shot
            show_ocr_loading_indicator()

            def ocr_worker():
                try:
                    ocr_lang = settings.ocr_lang if settings else "rus+eng"
                    ocr_eng = settings.ocr_engine if settings else "auto"
                    engine = create_ocr_engine(engine_name=ocr_eng, language=ocr_lang, logger=self._logger)
                    res = engine.recognize(shot.image_path)
                except Exception as exc:
                    self._logger.error("Windows Media OCR recognition error: %s", exc)
                    res = OCRResult(
                        raw_text=f"[Ошибка OCR: {exc}]",
                        cleaned_text="",
                        image_path=shot.image_path,
                    )

                def on_ocr_done():
                    if is_destroyed.is_set():
                        return
                    hide_ocr_loading_indicator()
                    cached_ocr_result[0] = res
                    cached_ocr_box[0] = box
                    if box is not None:
                        crop_pil = full_desktop_img.crop(box).convert("RGB") if full_desktop_img and box else None
                        text_layer.load_from_ocr(
                            res,
                            box,
                            crop_img=crop_pil,
                            cutout_item_id=cutout_img_id,
                            original_crop_photo=getattr(canvas, "_cutout_photo", None),
                        )
                        text_layer.show()
                        nonlocal current_state
                        current_state = "text_select"

                        if clipboard and settings and getattr(settings, "auto_copy", False) and res.cleaned_text:
                            clipboard.copy(res.cleaned_text)
                        if db and res.cleaned_text:
                            db.add_history(image_path=str(shot.image_path), ocr_text=res.cleaned_text)

                post_to_ui(on_ocr_done)

            threading.Thread(target=ocr_worker, daemon=True).start()

        def action_translate():
            nonlocal current_state
            # Toggle off if already in translate selection mode
            if current_state == "translate_select":
                text_layer.hide()
                lens_hud.reset_active_mode()
                current_state = "adjusting"
                return

            clear_active_card()
            lens_hud.set_active_mode("translate")

            if box is None:
                return

            target = settings.translate_lang if settings else "ru"

            # Re-use already cached translation if box and target language unchanged
            if (
                cached_tr_box[0] == box
                and cached_tr_lang[0] == target
                and cached_tr_result[0] is not None
                and cached_ocr_result[0] is not None
            ):
                crop_pil = full_desktop_img.crop(box).convert("RGB") if full_desktop_img and box else None
                text_layer.load_translation(
                    cached_ocr_result[0],
                    cached_tr_result[0],
                    box,
                    crop_img=crop_pil,
                    cutout_item_id=cutout_img_id,
                    original_crop_photo=getattr(canvas, "_cutout_photo", None),
                )
                text_layer.show()
                current_state = "translate_select"
                return

            t_path, shot = crop_and_save_current_selection()
            shot_ref[0] = shot
            show_ocr_loading_indicator("Перевод текста...")

            def tr_worker():
                try:
                    ocr_res = None
                    if cached_ocr_box[0] == box and cached_ocr_result[0] is not None:
                        ocr_res = cached_ocr_result[0]
                    else:
                        ocr_lang = settings.ocr_lang if settings else "rus+eng"
                        ocr_eng = settings.ocr_engine if settings else "auto"
                        engine = create_ocr_engine(engine_name=ocr_eng, language=ocr_lang, logger=self._logger)
                        ocr_res = engine.recognize(shot.image_path)
                        cached_ocr_result[0] = ocr_res
                        cached_ocr_box[0] = box

                    raw_text = ocr_res.cleaned_text
                    if raw_text:
                        tr_text = translate_text(raw_text, target_lang=target, logger=self._logger)
                    else:
                        tr_text = "[Нет текста для перевода]"
                except Exception as exc:
                    self._logger.error("Ошибка распознавания или перевода: %s", exc)
                    if ocr_res is None:
                        ocr_res = OCRResult(
                            raw_text=f"[Ошибка: {exc}]",
                            cleaned_text="",
                            image_path=shot.image_path,
                        )
                    tr_text = f"[Ошибка перевода: {exc}]"

                def on_tr_done():
                    if is_destroyed.is_set():
                        return
                    hide_ocr_loading_indicator()
                    cached_tr_result[0] = tr_text
                    cached_tr_lang[0] = target
                    cached_tr_box[0] = box
                    if box is not None:
                        crop_pil = full_desktop_img.crop(box).convert("RGB") if full_desktop_img and box else None
                        text_layer.load_translation(
                            ocr_res,
                            tr_text,
                            box,
                            crop_img=crop_pil,
                            cutout_item_id=cutout_img_id,
                            original_crop_photo=getattr(canvas, "_cutout_photo", None),
                        )
                        text_layer.show()
                        nonlocal current_state
                        current_state = "translate_select"

                        if clipboard and settings and getattr(settings, "auto_copy", False) and tr_text:
                            clipboard.copy(tr_text)
                        if db and raw_text:
                            db.add_history(image_path=str(shot.image_path), ocr_text=raw_text, translated_text=tr_text)

                post_to_ui(on_tr_done)

            threading.Thread(target=tr_worker, daemon=True).start()

        def action_lens():
            clear_active_card()
            lens_hud.set_active_mode("lens")
            t_path, shot = crop_and_save_current_selection()
            threading.Thread(
                target=lambda: open_in_google_lens(t_path, logger=self._logger),
                daemon=True,
            ).start()
            safe_close_overlay(delay_ms=80)

        def action_ai():
            clear_active_card()
            lens_hud.set_active_mode("ai")
            t_path, shot = crop_and_save_current_selection()

            hw, hh = lens_hud.get_dimensions()
            controller = RoundedCardController(
                canvas=canvas,
                box=box if box else (0, 0, 100, 100),
                hud_pos=(lens_hud.x, lens_hud.y, hw, hh),
                screen_size=(vw, vh),
                min_w=480,
                pad_x=18,
                pad_y=14,
            )
            active_controller[0] = controller
            card_frame = controller.content_frame

            title_f = tk.Frame(card_frame, bg="#222428")
            title_f.pack(fill=tk.X)
            tk.Label(title_f, text="Анализ Gemini Vision", font=("Segoe UI", 10, "bold"), fg="#ffffff", bg="#222428").pack(side=tk.LEFT)

            ent = tk.Entry(
                card_frame,
                bg="#181a1d",
                fg="#ffffff",
                font=("Segoe UI", 9),
                relief=tk.FLAT,
                highlightthickness=1,
                highlightbackground="#36393e",
                insertbackground="#ffffff",
            )
            ent.insert(0, "Объясни, что здесь изображено / как решить ошибку")
            ent.pack(fill=tk.X, pady=6)

            txt_res = tk.Text(
                card_frame,
                wrap=tk.WORD,
                height=6,
                width=50,
                bg="#181a1d",
                fg="#e0e0e0",
                insertbackground="#ffffff",
                selectbackground="#404349",
                selectforeground="#ffffff",
                relief=tk.FLAT,
                font=("Segoe UI", 9),
                padx=10,
                pady=8,
                highlightthickness=1,
                highlightbackground="#36393e",
            )
            txt_res.pack(fill=tk.X, expand=False, pady=6)

            key_frame = tk.Frame(card_frame, bg="#222428")

            def on_ask_click(event: Optional[tk.Event] = None):
                prompt = ent.get().strip()
                if not prompt:
                    prompt = "Объясни, что здесь изображено / как решить ошибку"

                txt_res.delete("1.0", tk.END)
                txt_res.insert(tk.END, "Запрос к Gemini Vision...\n")
                controller.update_geometry()

                def ai_worker():
                    try:
                        fresh_settings = db.get_settings() if db else (settings or None)
                        g_key = (fresh_settings.gemini_api_key if fresh_settings else "").strip()
                        g_model = fresh_settings.gemini_model if fresh_settings else "gemini-3.5-flash-lite"
                        g_proxy = (fresh_settings.gemini_proxy if fresh_settings else "").strip()
                        g_dns_ai = fresh_settings.gemini_use_dns_ai if fresh_settings else True
                        client = GeminiVisionClient(
                            api_key=g_key or None,
                            model=g_model,
                            proxy_url=g_proxy or None,
                            use_dns_ai=g_dns_ai,
                            logger=self._logger,
                        )
                        if not client.is_configured:
                            def show_key_input():
                                txt_res.delete("1.0", tk.END)
                                txt_res.insert(tk.END, "Ключ Gemini не найден! Вставьте Google AI Studio ключ ниже:")
                                for w in key_frame.winfo_children():
                                    w.destroy()
                                key_frame.pack(fill=tk.X, pady=4)
                                k_ent = tk.Entry(key_frame, bg="#181a1d", fg="#ffffff", relief=tk.FLAT, highlightthickness=1, highlightbackground="#36393e", insertbackground="#ffffff")
                                k_ent.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

                                def save_k(ev: Optional[tk.Event] = None):
                                    val = k_ent.get().strip()
                                    if val:
                                        if db:
                                            cur = db.get_settings()
                                            cur.gemini_api_key = val
                                            db.save_settings(cur)
                                        key_frame.pack_forget()
                                        on_ask_click()

                                k_ent.bind("<Return>", lambda e: (save_k(), "break")[1])
                                CapsuleButton(key_frame, text="Сохранить", style="primary", card_bg="#222428", command=save_k, height=28).pack(side=tk.RIGHT)
                                controller.update_geometry()

                            post_to_ui(show_key_input)
                            return

                        ans = client.analyze_image(shot.image_path, prompt=prompt)

                        def set_ans(res_text=ans, ctrl=controller):
                            if is_destroyed.is_set():
                                return
                            txt_res.delete("1.0", tk.END)
                            txt_res.insert(tk.END, res_text)
                            lines = len(res_text.splitlines())
                            txt_res.config(height=min(10, max(5, lines)))
                            ctrl.update_geometry()
                            if db:
                                db.add_history(image_path=str(shot.image_path), ocr_text="", gemini_result=res_text)

                        post_to_ui(set_ans)
                    except Exception as exc:
                        err_msg = str(exc)
                        self._logger.exception("Ошибка при обращении к Gemini Vision: %s", err_msg)

                        def show_err(msg=err_msg, ctrl=controller):
                            if is_destroyed.is_set():
                                return
                            txt_res.delete("1.0", tk.END)
                            txt_res.insert(tk.END, f"Ошибка: {msg}")
                            lines = len(msg.splitlines())
                            txt_res.config(height=min(10, max(5, lines)))
                            ctrl.update_geometry()

                        post_to_ui(show_err)

                threading.Thread(target=ai_worker, daemon=True).start()
                return "break"

            ent.bind("<Return>", on_ask_click)

            def copy_ai():
                val = txt_res.get("1.0", tk.END).strip()
                if clipboard:
                    clipboard.copy(val)
                if sound_fn and settings and settings.sound_enabled:
                    sound_fn(True)
                lbl_notify.config(text="✓ Скопировано!", fg="#ffffff")
                root.after(1500, lambda: lbl_notify.config(text="") if not is_destroyed.is_set() else None)

            btn_box = tk.Frame(card_frame, bg="#222428")
            btn_box.pack(fill=tk.X, pady=(2, 0))
            CapsuleButton(btn_box, text="Спросить", style="primary", card_bg="#222428", command=on_ask_click, height=30).pack(side=tk.LEFT, padx=(0, 6))
            CapsuleButton(btn_box, text="Скопировать", style="secondary", card_bg="#222428", command=copy_ai, height=30).pack(side=tk.LEFT, padx=(0, 6))
            lbl_notify = tk.Label(btn_box, text="", font=("Segoe UI", 8), fg="#ffffff", bg="#222428")
            lbl_notify.pack(side=tk.LEFT, padx=6)
            CapsuleButton(btn_box, text="Закрыть", style="danger", card_bg="#222428", command=clear_active_card, height=30).pack(side=tk.RIGHT)

            controller.update_geometry()

        def action_copy():
            if current_state in ("text_select", "translate_select") and text_layer.is_active:
                text_layer.execute_copy()
                return

            t_path, shot = crop_and_save_current_selection()
            bx1, by1, bx2, by2 = box if box else (0, 0, 100, 100)
            if full_desktop_img:
                cropped = full_desktop_img.crop((bx1, by1, bx2, by2)).convert("RGB")
                copy_image_to_clipboard(cropped)
            elif shot and shot.image_path:
                copy_image_to_clipboard(shot.image_path)

            if sound_fn and settings and settings.sound_enabled:
                sound_fn(True)

            # Show quick confirmation stadium capsule toast
            show_copied_toast("✓ Изображение скопировано!")

        def action_close():
            self._logger.info("Selection closed by user.")
            self._selected_area = None
            safe_close_overlay(0)

        # Dual Capsule Floating HUD Toolbar
        lens_hud = GoogleLensHUD(
            parent=canvas,
            on_text=action_text,
            on_translate=action_translate,
            on_lens=action_lens,
            on_ai=action_ai,
            on_copy=action_copy,
            on_close=action_close,
            ai_enabled=settings.ai_enabled if settings else True,
        )

        def position_hud(animate: bool = False):
            if box is None:
                return
            hw, hh = lens_hud.get_dimensions()
            target_hx, target_hy = get_hud_placement(box[0], box[1], box[2], box[3], hw, hh, vw, vh, margin=14)

            if not animate:
                lens_hud.draw(target_hx, target_hy)
            else:
                start_hy = min(vh - hh - 14, target_hy + 8)

                def slide_step(step=0):
                    if is_destroyed.is_set() or box is None:
                        return
                    curr_y = start_hy - int((start_hy - target_hy) * (step / 3.0))
                    lens_hud.draw(target_hx, curr_y)
                    if step < 3:
                        root.after(20, lambda: slide_step(step + 1))

                slide_step(1)

        # Mouse Event Handlers
        def on_press(event: tk.Event) -> None:
            nonlocal current_state, box, drag_handle, drag_start_mx, drag_start_my, orig_box
            try:
                canvas.focus_set()
            except Exception:
                pass
            drag_start_mx = event.x
            drag_start_my = event.y

            # Handle HUD button or capsule click directly
            if lens_hud.is_inside(event.x, event.y):
                lens_hud.handle_click(event.x, event.y)
                return

            if current_state in ("text_select", "translate_select"):
                text_layer.handle_press(event.x, event.y)
                return

            if current_state == "adjusting" and box is not None:
                target = hit_test_lens(event.x, event.y, box, corner_hitbox=28)
                if target in (HANDLE_NW, HANDLE_NE, HANDLE_SE, HANDLE_SW):
                    current_state = "dragging"
                    drag_handle = target
                    orig_box = box
                    invalidate_ocr_cache()
                    clear_active_card()
                    return
                elif target == "move":
                    current_state = "dragging"
                    drag_handle = "move"
                    orig_box = box
                    invalidate_ocr_cache()
                    clear_active_card()
                    return
                else:
                    invalidate_ocr_cache()
                    clear_active_card()
                    canvas.delete("lens_corners")
                    canvas.delete("lens_badge")
                    lens_hud.hide()
                    box = None

            current_state = "selecting"
            drag_handle = "create"
            orig_box = (event.x, event.y, event.x, event.y)
            invalidate_ocr_cache()
            clear_active_card()
            lens_hud.hide()

        def on_drag(event: tk.Event) -> None:
            nonlocal current_state, box, drag_handle, orig_box
            if current_state in ("text_select", "translate_select"):
                text_layer.handle_drag(event.x, event.y)
                return

            if current_state == "selecting":
                bx1 = min(orig_box[0], event.x)
                by1 = min(orig_box[1], event.y)
                bx2 = max(orig_box[0], event.x)
                by2 = max(orig_box[1], event.y)
                box = (bx1, by1, bx2, by2)
                update_cutout_preview(bx1, by1, bx2, by2)
                draw_corners_and_badge(bx1, by1, bx2, by2)

            elif current_state == "dragging" and box is not None:
                dx = event.x - drag_start_mx
                dy = event.y - drag_start_my
                new_box = apply_drag_lens(drag_handle, orig_box, dx, dy, min_w=24, min_h=24, max_w=vw, max_h=vh)
                box = new_box
                update_cutout_preview(new_box[0], new_box[1], new_box[2], new_box[3])
                draw_corners_and_badge(new_box[0], new_box[1], new_box[2], new_box[3])
                position_hud(animate=False)

        def on_release(event: tk.Event) -> None:
            nonlocal current_state, box, drag_handle
            if current_state in ("text_select", "translate_select"):
                text_layer.handle_release(event.x, event.y)
                return

            if current_state == "selecting":
                if box is None:
                    current_state = "idle"
                    return
                w = box[2] - box[0]
                h = box[3] - box[1]
                if w < 16 or h < 16:
                    canvas.delete("lens_corners")
                    canvas.delete("lens_badge")
                    lens_hud.hide()
                    box = None
                    current_state = "idle"
                    return

                current_state = "adjusting"
                draw_corners_and_badge(box[0], box[1], box[2], box[3])
                position_hud(animate=True)

            elif current_state == "dragging":
                current_state = "adjusting"
                drag_handle = "none"
                if box is not None:
                    draw_corners_and_badge(box[0], box[1], box[2], box[3])
                    position_hud(animate=False)

        def on_motion(event: tk.Event) -> None:
            if lens_hud.is_inside(event.x, event.y):
                canvas.config(cursor="hand2")
                root.config(cursor="hand2")
                lens_hud.handle_motion(event.x, event.y)
                return
            else:
                lens_hud.clear_hover()

            if current_state in ("text_select", "translate_select"):
                cursor = text_layer.handle_motion(event.x, event.y)
                canvas.config(cursor=cursor)
                root.config(cursor=cursor)
                return

            if current_state == "adjusting" and box is not None:
                target = hit_test_lens(event.x, event.y, box, corner_hitbox=28)
                cursor = CURSOR_MAP.get(target, "cross")
                canvas.config(cursor=cursor)
                root.config(cursor=cursor)
            elif current_state == "idle":
                canvas.config(cursor="cross")
                root.config(cursor="cross")

        def on_double_click(event: tk.Event) -> None:
            if current_state in ("text_select", "translate_select"):
                text_layer.handle_double_click(event.x, event.y)

        def on_triple_click(event: tk.Event) -> None:
            if current_state in ("text_select", "translate_select"):
                text_layer.handle_triple_click(event.x, event.y)

        def on_key_press(event: tk.Event) -> Optional[str]:
            code = getattr(event, "keycode", 0)
            keysym = getattr(event, "keysym", "").lower()
            char = getattr(event, "char", "")
            is_ctrl = bool(event.state & 4)

            # Ctrl+C or ASCII ETX (Ctrl+C character code 3)
            if (is_ctrl and (code == 67 or keysym in ("c", "с", "cyrillic_es"))) or char == "\x03":
                if current_state in ("text_select", "translate_select"):
                    text_layer.execute_copy()
                    return "break"

            # Ctrl+A or ASCII SOH (Ctrl+A character code 1)
            elif (is_ctrl and (code == 65 or keysym in ("a", "а", "cyrillic_a"))) or char == "\x01":
                if current_state in ("text_select", "translate_select"):
                    text_layer.select_all()
                    return "break"

            return None

        def on_key_enter(event: Optional[tk.Event] = None) -> None:
            if current_state == "adjusting" and box is not None:
                action_text()

        def on_cancel(event: Optional[tk.Event] = None) -> None:
            nonlocal current_state
            if current_state in ("text_select", "translate_select"):
                if text_layer.selected_indices:
                    text_layer.clear_selection()
                    return
                text_layer.hide()
                lens_hud.reset_active_mode()
                current_state = "adjusting"
                return
            action_close()

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        canvas.bind("<Motion>", on_motion)
        canvas.bind("<Double-Button-1>", on_double_click)
        canvas.bind("<Triple-Button-1>", on_triple_click)
        root.bind_all("<KeyPress>", on_key_press, add="+")
        canvas.bind_all("<KeyPress>", on_key_press, add="+")
        root.bind_all("<Control-c>", on_key_press, add="+")
        root.bind_all("<Control-C>", on_key_press, add="+")
        canvas.bind_all("<Control-c>", on_key_press, add="+")
        canvas.bind_all("<Control-C>", on_key_press, add="+")
        root.bind_all("<Control-Key-c>", on_key_press, add="+")
        root.bind_all("<Control-Key-C>", on_key_press, add="+")
        canvas.bind_all("<Control-Key-c>", on_key_press, add="+")
        canvas.bind_all("<Control-Key-C>", on_key_press, add="+")
        root.bind_all("<Escape>", on_cancel)
        canvas.bind_all("<Escape>", on_cancel)
        root.bind_all("<Return>", on_key_enter)
        canvas.bind_all("<Return>", on_key_enter)

        def cleanup_resources():
            nonlocal full_desktop_img
            is_destroyed.set()
            text_layer.clear()
            clear_active_card()
            if hasattr(canvas, "_bg_photo"):
                canvas._bg_photo = None
            if hasattr(canvas, "_cutout_photo"):
                canvas._cutout_photo = None
            if full_desktop_img is not None:
                try:
                    full_desktop_img.close()
                except Exception:
                    pass
                full_desktop_img = None
            try:
                canvas.delete("all")
            except Exception:
                pass
            import gc
            gc.collect()

        if sys.platform == "win32":
            root.geometry(f"{vw}x{vh}+{vx}+{vy}")
            root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.deiconify()
        root.attributes("-topmost", True)
        root.lift()
        root.focus_force()
        canvas.focus_set()

        try:
            root.wait_variable(done_var)
        finally:
            cleanup_resources()

        return self._selected_area
