"""AI Interface Components for ScreenLens.
Provides compact in-overlay AI panel and standalone multi-turn extended chat window.
"""

import base64
import logging
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from typing import Callable, Optional

from PIL import Image, ImageDraw, ImageTk

from core.gemini import ChatMessage, GeminiChatSession, GeminiVisionClient
from ui.floating_hud import CapsuleButton, RoundedCardController, calc_pill_width, render_rounded_rect_image


def create_rounded_thumbnail(
    image_source,
    target_size: tuple[int, int] = (50, 50),
    radius: int = 8,
) -> Optional[ImageTk.PhotoImage]:
    """Generates an anti-aliased thumbnail with rounded corners from Path or PIL Image."""
    try:
        if isinstance(image_source, (str, Path)):
            p = Path(image_source)
            if not p.is_file():
                return None
            img = Image.open(p).convert("RGB")
        elif isinstance(image_source, Image.Image):
            img = image_source.copy().convert("RGB")
        else:
            return None

        # Create thumbnail maintaining aspect ratio
        thumb = img.copy()
        thumb.thumbnail(target_size, Image.Resampling.LANCZOS)
        tw, th = thumb.size

        # Supersampled rounded mask
        scale = 3
        sw, sh = tw * scale, th * scale
        mask = Image.new("L", (sw, sh), 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle([0, 0, sw - 1, sh - 1], radius=radius * scale, fill=255)
        mask = mask.resize((tw, th), Image.Resampling.LANCZOS)

        rounded = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
        rounded.paste(thumb, (0, 0), mask=mask)
        return ImageTk.PhotoImage(rounded)
    except Exception:
        return None


def render_minimal_copy_icon(size: int = 15, color: tuple[int, int, int, int] = (148, 163, 184, 255)) -> Image.Image:
    """Generates a crisp, minimalist two-sheet vector copy icon."""
    scale = 4
    s = size * scale
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    th = max(1, int(1.2 * scale))
    r = int(1.6 * scale)
    # Background sheet
    d.rounded_rectangle(
        [int(4.5 * scale), int(1.2 * scale), int(13.8 * scale), int(10.5 * scale)],
        radius=r,
        outline=color,
        width=th,
    )
    # Foreground sheet
    d.rounded_rectangle(
        [int(1.2 * scale), int(4.5 * scale), int(10.5 * scale), int(13.8 * scale)],
        radius=r,
        fill=(24, 26, 29, 255),
        outline=color,
        width=th,
    )
    return im.resize((size, size), Image.Resampling.LANCZOS)


def render_minimal_check_icon(size: int = 15, color: tuple[int, int, int, int] = (74, 222, 128, 255)) -> Image.Image:
    """Generates a clean vector checkmark icon for copy confirmation."""
    scale = 4
    s = size * scale
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    th = max(1, int(1.6 * scale))
    pts = [(2.5 * scale, 7.8 * scale), (5.8 * scale, 11.5 * scale), (13.2 * scale, 3.5 * scale)]
    d.line(pts, fill=color, width=th, joint="curve")
    return im.resize((size, size), Image.Resampling.LANCZOS)


class DarkScrollbar(tk.Canvas):
    """Minimalist dark scrollbar styled seamlessly to match the dark application theme."""

    def __init__(
        self,
        parent: tk.Misc,
        target: tk.Text,
        width: int = 6,
        bg: str = "#181a1d",
        thumb_color: str = "#3c4048",
        thumb_hover: str = "#5a606d",
        **kwargs,
    ) -> None:
        super().__init__(parent, width=width, bg=bg, highlightthickness=0, bd=0, **kwargs)
        self.target = target
        self.w = width
        self.thumb_color = thumb_color
        self.thumb_hover = thumb_hover
        self._current_thumb = thumb_color
        self._first = 0.0
        self._last = 1.0
        self._drag_data = {"y": 0, "first": 0.0}

        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)

    def set(self, first: str | float, last: str | float) -> None:
        self._first = float(first)
        self._last = float(last)
        self._draw()

    def _draw(self) -> None:
        self.delete("thumb")
        h = self.winfo_height()
        # Content fully fits -> hide scrollbar thumb cleanly
        if h <= 10 or (self._first <= 0.001 and self._last >= 0.999):
            return

        pad = 1
        y1 = max(pad, self._first * h)
        y2 = min(h - pad, self._last * h)
        min_thumb = 18
        if (y2 - y1) < min_thumb:
            center = (y1 + y2) / 2
            y1 = max(pad, center - min_thumb / 2)
            y2 = min(h - pad, y1 + min_thumb)
            if y2 > h - pad:
                y2 = h - pad
                y1 = max(pad, y2 - min_thumb)

        r = (self.w - 2 * pad) // 2
        if r > 0 and (y2 - y1) > 2 * r:
            self.create_oval(pad, y1, self.w - pad, y1 + 2 * r, fill=self._current_thumb, outline="", tags="thumb")
            self.create_rectangle(pad, y1 + r, self.w - pad, y2 - r, fill=self._current_thumb, outline="", tags="thumb")
            self.create_oval(pad, y2 - 2 * r, self.w - pad, y2, fill=self._current_thumb, outline="", tags="thumb")
        else:
            self.create_rectangle(pad, y1, self.w - pad, y2, fill=self._current_thumb, outline="", tags="thumb")

    def _on_enter(self, _event: Optional[tk.Event] = None) -> None:
        self._current_thumb = self.thumb_hover
        self.itemconfig("thumb", fill=self._current_thumb)

    def _on_leave(self, _event: Optional[tk.Event] = None) -> None:
        self._current_thumb = self.thumb_color
        self.itemconfig("thumb", fill=self._current_thumb)

    def _on_press(self, event: tk.Event) -> None:
        h = self.winfo_height()
        if h <= 0:
            return
        y1 = self._first * h
        y2 = self._last * h
        if y1 <= event.y <= y2:
            self._drag_data["y"] = event.y
            self._drag_data["first"] = self._first
        else:
            span = max(0.05, self._last - self._first)
            new_first = max(0.0, min(1.0 - span, (event.y / h) - span / 2))
            self.target.yview_moveto(new_first)
            self._drag_data["y"] = event.y
            self._drag_data["first"] = new_first

    def _on_drag(self, event: tk.Event) -> None:
        h = self.winfo_height()
        if h <= 0:
            return
        dy = event.y - self._drag_data["y"]
        span = self._last - self._first
        dfirst = dy / h
        new_first = max(0.0, min(1.0 - span, self._drag_data["first"] + dfirst))
        self.target.yview_moveto(new_first)


class RoundedInputBox(tk.Canvas):
    """Input container with anti-aliased rounded corners, hosting question entry and send button."""

    def __init__(
        self,
        parent: tk.Misc,
        height: int = 36,
        radius: int = 12,
        bg_card: str = "#222428",
        bg_fill: tuple[int, int, int, int] = (24, 26, 29, 255),
        outline: tuple[int, int, int, int] = (54, 57, 62, 255),
        **kwargs,
    ) -> None:
        super().__init__(parent, height=height, bg=bg_card, highlightthickness=0, bd=0, **kwargs)
        self.box_h = height
        self.radius = radius
        self.bg_fill = bg_fill
        self.outline = outline
        self._last_w = 0
        self._bg_photo: Optional[ImageTk.PhotoImage] = None

        self.bg_id = self.create_image(0, 0, anchor=tk.NW)

        self.ent = tk.Entry(
            self,
            bg="#181a1d",
            fg="#ffffff",
            font=("Segoe UI", 10),
            relief=tk.FLAT,
            bd=0,
            insertbackground="#ffffff",
        )
        self.ent_win = self.create_window(12, (height - 22) // 2, window=self.ent, anchor=tk.NW)

        self.btn_send = CapsuleButton(
            self,
            text="Спросить",
            style="primary",
            card_bg="#181a1d",
            height=26,
            font=("Segoe UI", 9, "bold"),
        )
        self.btn_win = self.create_window(100, (height - 26) // 2, window=self.btn_send, anchor=tk.NW)

        self.bind("<Configure>", self._on_configure)

    def _on_configure(self, event: tk.Event) -> None:
        w = event.width
        if w < 50 or w == self._last_w:
            return
        self._last_w = w
        img = render_rounded_rect_image(
            w, self.box_h, radius=self.radius, fill=self.bg_fill, outline=self.outline, outline_width=1.0
        )
        self._bg_photo = ImageTk.PhotoImage(img)
        self.itemconfig(self.bg_id, image=self._bg_photo)

        btn_w = 70
        self.coords(self.ent_win, 12, (self.box_h - 22) // 2)
        self.itemconfigure(self.ent_win, width=max(40, w - btn_w - 24), height=22)

        self.coords(self.btn_win, w - btn_w - 6, (self.box_h - 26) // 2)
        self.itemconfigure(self.btn_win, width=btn_w, height=26)


class RoundedResponseBox(tk.Canvas):
    """Response container with anti-aliased rounded corners, hosting header, text view, and custom scrollbar."""

    def __init__(
        self,
        parent: tk.Misc,
        height: int = 120,
        radius: int = 12,
        bg_card: str = "#222428",
        bg_fill: tuple[int, int, int, int] = (24, 26, 29, 255),
        outline: tuple[int, int, int, int] = (54, 57, 62, 255),
        **kwargs,
    ) -> None:
        super().__init__(parent, height=height, bg=bg_card, highlightthickness=0, bd=0, **kwargs)
        self.box_h = height
        self.radius = radius
        self.bg_fill = bg_fill
        self.outline = outline
        self._last_w = 0
        self._bg_photo: Optional[ImageTk.PhotoImage] = None

        self.bg_id = self.create_image(0, 0, anchor=tk.NW)

        # 1. Header Frame
        self.hdr = tk.Frame(self, bg="#181a1d")
        self.lbl_title = tk.Label(
            self.hdr,
            text="Ответ Gemini",
            font=("Segoe UI", 9, "bold"),
            fg="#64748b",
            bg="#181a1d",
        )
        self.lbl_title.pack(side=tk.LEFT)

        self.lbl_notify = tk.Label(
            self.hdr,
            text="",
            font=("Segoe UI", 8),
            fg="#4ade80",
            bg="#181a1d",
        )
        self.lbl_notify.pack(side=tk.LEFT, padx=6)

        # Minimalist vector copy icons
        self._copy_norm = ImageTk.PhotoImage(render_minimal_copy_icon(15, (148, 163, 184, 255)))
        self._copy_hov = ImageTk.PhotoImage(render_minimal_copy_icon(15, (255, 255, 255, 255)))
        self._copy_check = ImageTk.PhotoImage(render_minimal_check_icon(15, (74, 222, 128, 255)))

        self.btn_copy = tk.Label(
            self.hdr,
            image=self._copy_norm,
            bg="#181a1d",
            cursor="hand2",
            padx=4,
            pady=2,
        )
        self.btn_copy.pack(side=tk.RIGHT)
        self.btn_copy.bind("<Enter>", lambda e: self.btn_copy.config(image=self._copy_hov))
        self.btn_copy.bind("<Leave>", lambda e: self.btn_copy.config(image=self._copy_norm))

        self.hdr_win = self.create_window(10, 6, window=self.hdr, anchor=tk.NW)

        # 2. Text Area
        self.txt_res = tk.Text(
            self,
            wrap=tk.WORD,
            bg="#181a1d",
            fg="#e2e8f0",
            insertbackground="#ffffff",
            selectbackground="#3b82f6",
            selectforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            font=("Segoe UI", 10),
            padx=6,
            pady=4,
            highlightthickness=0,
        )
        self.txt_win = self.create_window(8, 32, window=self.txt_res, anchor=tk.NW)

        # 3. Custom Dark Scrollbar
        self.scrollbar = DarkScrollbar(self, self.txt_res, width=6, bg="#181a1d")
        self.sb_win = self.create_window(100, 32, window=self.scrollbar, anchor=tk.NW)
        self.txt_res.config(yscrollcommand=self.scrollbar.set)

        def _on_wheel(e):
            self.txt_res.yview_scroll(int(-1 * (e.delta / 120)), "units")
        self.txt_res.bind("<MouseWheel>", _on_wheel)

        self.bind("<Configure>", self._on_configure)

    def set_box_height(self, h: int) -> None:
        """Updates box height and triggers redraw."""
        if h <= 30:
            return
        self.box_h = h
        self.config(height=h)
        self._layout_widgets(self._last_w, h)

    def _on_configure(self, event: tk.Event) -> None:
        w = event.width
        h = event.height
        if w < 50 or h < 30:
            return
        if w == self._last_w and h == self.box_h and self._bg_photo is not None:
            return
        self._last_w = w
        self.box_h = h
        self._layout_widgets(w, h)

    def _layout_widgets(self, w: int, h: int) -> None:
        if w < 50 or h < 30:
            return
        img = render_rounded_rect_image(
            w, h, radius=self.radius, fill=self.bg_fill, outline=self.outline, outline_width=1.0
        )
        self._bg_photo = ImageTk.PhotoImage(img)
        self.itemconfig(self.bg_id, image=self._bg_photo)

        self.coords(self.hdr_win, 10, 6)
        self.itemconfigure(self.hdr_win, width=w - 20, height=22)

        content_h = max(20, h - 38)
        self.coords(self.txt_win, 8, 32)
        self.itemconfigure(self.txt_win, width=w - 24, height=content_h)

        self.coords(self.sb_win, w - 12, 32)
        self.itemconfigure(self.sb_win, width=6, height=content_h)


class AIPanelController:
    """Manages the compact Gemini AI card rendered directly inside the screen overlay.
    Provides thumbnail preview, quick action prompts, custom user input, scrollable
    response area, and controls for copying, continuing multi-turn dialog, and opening extended chat.
    """

    QUICK_PROMPTS = [
        ("Объяснить", "Опиши подробно, что изображено на этом снимке экрана, выдели ключевые детали и суть."),
        ("Текст", "Извлеки весь текст со снимка экрана, максимально точно сохранив структуру, строки и форматирование."),
        ("Перевод", "Переведи весь текст на снимке на русский язык, сохраняя смысл технических терминов и контекст."),
        ("Код", "Объясни назначение этого фрагмента кода, укажи на ошибки или узкие места и предложи улучшенный вариант."),
    ]

    def __init__(
        self,
        controller: RoundedCardController,
        session: GeminiChatSession,
        crop_image: Optional[Image.Image] = None,
        db=None,
        settings=None,
        sound_fn: Optional[Callable[[bool], None]] = None,
        on_close: Optional[Callable[[], None]] = None,
        on_open_extended: Optional[Callable[[GeminiChatSession], None]] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.controller = controller
        self.session = session
        self.crop_image = crop_image
        self.db = db
        self.settings = settings
        self.sound_fn = sound_fn
        self.on_close = on_close or (lambda: None)
        self.on_open_extended = on_open_extended or (lambda s: None)
        self._logger = logger or logging.getLogger(__name__)

        self.card_frame = controller.content_frame
        self.is_loading = False
        self._last_prompt: str = ""
        self._thumb_photo: Optional[ImageTk.PhotoImage] = None

        self._ui_queue: queue.Queue = queue.Queue()
        self._schedule_ui_queue()

        self._build_ui()

    def _schedule_ui_queue(self) -> None:
        """Polls UI queue on Tkinter main thread every 25ms."""
        def _poll():
            try:
                while True:
                    fn = self._ui_queue.get_nowait()
                    try:
                        fn()
                    except Exception as exc:
                        self._logger.exception("AIPanel UI queue task error: %s", exc)
            except queue.Empty:
                pass
            except Exception:
                pass
            try:
                if self.card_frame.winfo_exists():
                    self.card_frame.after(25, _poll)
            except Exception:
                pass

        try:
            self.card_frame.after(25, _poll)
        except Exception:
            pass

    def _post_to_ui(self, fn: Callable[[], None]) -> None:
        """Dispatches callback from worker threads safely to the main thread."""
        self._ui_queue.put(fn)

    def _build_ui(self) -> None:
        """Constructs compact AI panel widgets tailored for side placement."""
        for w in self.card_frame.winfo_children():
            w.destroy()

        # 1. Header Frame: Thumbnail + Model Info + Extended Chat button + Close button
        header_f = tk.Frame(self.card_frame, bg="#222428")
        header_f.pack(fill=tk.X, pady=(0, 6))

        # Thumbnail (enlarged 44x44)
        thumb_img = self.crop_image if self.crop_image else self.session.image_path
        self._thumb_photo = create_rounded_thumbnail(thumb_img, target_size=(44, 44), radius=6)
        if self._thumb_photo:
            lbl_thumb = tk.Label(header_f, image=self._thumb_photo, bg="#222428", bd=0)
            lbl_thumb.pack(side=tk.LEFT, padx=(0, 8))

        # Title & Subtitle column
        title_col = tk.Frame(header_f, bg="#222428")
        title_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tk.Label(
            title_col,
            text="Gemini Vision",
            font=("Segoe UI", 10, "bold"),
            fg="#ffffff",
            bg="#222428",
        ).pack(anchor=tk.W)

        sub_f = tk.Frame(title_col, bg="#222428")
        sub_f.pack(anchor=tk.W, pady=(1, 0))

        model_name = getattr(self.session.client, "_model", "gemini-3.5-flash-lite")
        disp_model = model_name.replace("gemini-", "")
        tk.Label(
            sub_f,
            text=f"{disp_model}",
            font=("Segoe UI", 9),
            fg="#9aa0a6",
            bg="#222428",
        ).pack(side=tk.LEFT)

        self.lbl_status = tk.Label(
            sub_f,
            text=" • Готов",
            font=("Segoe UI", 9),
            fg="#4ade80",
            bg="#222428",
        )
        self.lbl_status.pack(side=tk.LEFT)

        # Close button in header (rightmost)
        btn_close_header = tk.Label(
            header_f,
            text="✕",
            font=("Segoe UI", 11, "bold"),
            fg="#a0a5ad",
            bg="#222428",
            cursor="hand2",
            padx=6,
            pady=3,
        )
        btn_close_header.pack(side=tk.RIGHT, anchor=tk.NE)
        btn_close_header.bind("<Enter>", lambda e: btn_close_header.config(fg="#ffffff"))
        btn_close_header.bind("<Leave>", lambda e: btn_close_header.config(fg="#a0a5ad"))
        btn_close_header.bind("<Button-1>", lambda e: self.on_close())

        # Relocated extended chat button in header: "Чат ↗"
        self.btn_extended = CapsuleButton(
            header_f,
            text="Чат ↗",
            style="tonal",
            card_bg="#222428",
            height=28,
            font=("Segoe UI", 9, "bold"),
            command=self._open_extended_chat,
        )
        self.btn_extended.pack(side=tk.RIGHT, padx=(0, 6))

        # 2. Quick Action Buttons: 1 single row across 4 columns
        quick_grid = tk.Frame(self.card_frame, bg="#222428")
        quick_grid.pack(fill=tk.X, pady=(2, 6))
        for col in range(4):
            quick_grid.columnconfigure(col, weight=1)

        self.quick_buttons = []
        for idx, (label, prompt_val) in enumerate(self.QUICK_PROMPTS):
            btn = CapsuleButton(
                quick_grid,
                text=label,
                style="tonal",
                card_bg="#222428",
                height=28,
                width=76,
                font=("Segoe UI", 9, "bold"),
                command=lambda p=prompt_val: self._on_quick_action(p),
            )
            btn.grid(row=0, column=idx, sticky="ew", padx=2, pady=2)
            self.quick_buttons.append(btn)

        # 3. Custom Query Input Box (Rounded)
        self.input_box = RoundedInputBox(self.card_frame, height=36, radius=12)
        self.input_box.pack(fill=tk.X, pady=(2, 6))

        self.ent = self.input_box.ent
        self.btn_send = self.input_box.btn_send
        self.btn_send.command = self._submit_query

        self.ent.insert(0, "Вопрос по снимку...")
        self._placeholder_active = True

        def _on_entry_focus_in(e):
            if self._placeholder_active:
                self.ent.delete(0, tk.END)
                self.ent.config(fg="#ffffff")
                self._placeholder_active = False

        def _on_entry_focus_out(e):
            if not self.ent.get().strip():
                self.ent.insert(0, "Вопрос по снимку...")
                self.ent.config(fg="#80868b")
                self._placeholder_active = True

        self.ent.bind("<FocusIn>", _on_entry_focus_in)
        self.ent.bind("<FocusOut>", _on_entry_focus_out)
        self.ent.bind("<Return>", lambda e: self._submit_query())

        # 4. Response Area (Rounded with Minimal Copy Icon and Dark Scrollbar)
        self.resp_box = RoundedResponseBox(self.card_frame, height=120, radius=12)
        self.resp_f = self.resp_box
        self.txt_res = self.resp_box.txt_res
        self.lbl_notify = self.resp_box.lbl_notify
        self.btn_copy = self.resp_box.btn_copy
        self.btn_copy.bind("<Button-1>", lambda e: self._copy_response())
        self.scrollbar = self.resp_box.scrollbar

        # Key Input Frame (for missing API key prompt)
        self.key_frame = tk.Frame(self.card_frame, bg="#222428")
        self.btn_continue = None

        # Only reveal response frame if dialog already has a previous response
        last_resp = self.session.get_last_response()
        if last_resp:
            self.txt_res.insert(tk.END, last_resp)
            self._update_response_height(last_resp)
            self.resp_f.pack(fill=tk.X, pady=(2, 4))

        self.controller.update_geometry()

    def _on_quick_action(self, prompt: str) -> None:
        """Triggered when user clicks a quick action chip."""
        if self.is_loading:
            return
        self.ent.delete(0, tk.END)
        self.ent.insert(0, prompt)
        self._placeholder_active = False
        self._submit_query()

    def _on_continue_dialog(self) -> None:
        """Prepares input field for next question in the multi-turn session."""
        if self.is_loading:
            return
        self.ent.delete(0, tk.END)
        self.ent.insert(0, "Следующий вопрос по этому снимку...")
        self.ent.config(fg="#80868b")
        self._placeholder_active = True
        self.ent.focus_set()

    def _set_loading_state(self, loading: bool) -> None:
        """Updates UI elements and prevents duplicate requests while active."""
        self.is_loading = loading
        if loading:
            self.lbl_status.config(text=" • Думает...", fg="#60a5fa")
            self.btn_send.config(state="disabled")
            for b in self.quick_buttons:
                b.config(state="disabled")
        else:
            self.lbl_status.config(text=" • Готов", fg="#4ade80")
            self.btn_send.config(state="normal")
            for b in self.quick_buttons:
                b.config(state="normal")

    def _update_response_height(self, text: str) -> None:
        """Dynamically adjusts text area height downwards to comfortably fit the response."""
        try:
            self.txt_res.update_idletasks()
            dl_tuple = self.txt_res.count("1.0", "end - 1c", "displaylines")
            display_lines = dl_tuple[0] if dl_tuple else len(text.splitlines())
        except Exception:
            display_lines = len(text.splitlines())

        card_cy = getattr(self.controller, "cy", 0)
        screen_h = getattr(self.controller, "screen_h", 1080)
        margin = 16
        avail_h = max(200, screen_h - card_cy - margin - 170)
        max_lines_screen = max(6, avail_h // 22)
        target_lines = min(max_lines_screen, max(4, display_lines))

        self.txt_res.config(height=target_lines)
        target_box_h = 38 + max(70, target_lines * 19)
        if hasattr(self, "resp_box"):
            self.resp_box.set_box_height(target_box_h)

    def _submit_query(self) -> None:
        """Sends the question to Gemini asynchronously."""
        if self.is_loading:
            return

        prompt = self.ent.get().strip()
        if self._placeholder_active or not prompt or prompt.startswith("Задайте вопрос") or prompt.startswith("Вопрос по снимку"):
            prompt = "Объясни подробно, что здесь изображено / как решить ошибку"

        self._last_prompt = prompt
        self._set_loading_state(True)

        if not self.resp_f.winfo_ismapped():
            self.resp_f.pack(fill=tk.X, pady=(2, 4))

        self.txt_res.delete("1.0", tk.END)
        self.txt_res.insert(tk.END, "Запрос к Gemini Vision...\nПодготовка ответа...")
        self.txt_res.config(height=3)
        if hasattr(self, "resp_box"):
            self.resp_box.set_box_height(86)
        self.controller.update_geometry()

        def worker():
            try:
                # Check API key configuration
                if not self.session.client.is_configured:
                    def show_key_input():
                        self._set_loading_state(False)
                        if not self.resp_f.winfo_ismapped():
                            self.resp_f.pack(fill=tk.X, pady=(2, 4))
                        self.txt_res.delete("1.0", tk.END)
                        self.txt_res.insert(tk.END, "Ключ Gemini не найден! Вставьте Google AI Studio ключ ниже:")
                        self.txt_res.config(height=3)
                        if hasattr(self, "resp_box"):
                            self.resp_box.set_box_height(86)
                        for child in self.key_frame.winfo_children():
                            child.destroy()
                        self.key_frame.pack(fill=tk.X, pady=4)
                        k_ent = tk.Entry(
                            self.key_frame,
                            bg="#181a1d",
                            fg="#ffffff",
                            relief=tk.FLAT,
                            highlightthickness=1,
                            highlightbackground="#36393e",
                            insertbackground="#ffffff",
                        )
                        k_ent.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

                        def save_key(ev=None):
                            val = k_ent.get().strip()
                            if val:
                                if self.db:
                                    cur = self.db.get_settings()
                                    cur.gemini_api_key = val
                                    self.db.save_settings(cur)
                                self.session.client._api_key = val
                                self.key_frame.pack_forget()
                                self._submit_query()

                        k_ent.bind("<Return>", lambda e: (save_key(), "break")[1])
                        CapsuleButton(
                            self.key_frame,
                            text="Сохранить",
                            style="primary",
                            card_bg="#222428",
                            command=save_key,
                            height=26,
                        ).pack(side=tk.RIGHT)
                        self.controller.update_geometry()

                    self._post_to_ui(show_key_input)
                    return

                ans = self.session.send_message(prompt)

                def on_success(res=ans):
                    self._set_loading_state(False)
                    if not self.resp_f.winfo_ismapped():
                        self.resp_f.pack(fill=tk.X, pady=(2, 4))
                    self.txt_res.delete("1.0", tk.END)
                    self.txt_res.insert(tk.END, res)
                    self._update_response_height(res)
                    self.controller.update_geometry()
                    if self.db:
                        self.db.add_history(
                            image_path=str(self.session.image_path),
                            ocr_text=self.session.ocr_text,
                            gemini_result=res,
                        )

                self._post_to_ui(on_success)
            except Exception as exc:
                err_msg = str(exc)
                self._logger.exception("Gemini Vision request error: %s", err_msg)

                def on_error(msg=err_msg):
                    self._set_loading_state(False)
                    if not self.resp_f.winfo_ismapped():
                        self.resp_f.pack(fill=tk.X, pady=(2, 4))
                    self.txt_res.delete("1.0", tk.END)
                    self.txt_res.insert(tk.END, f"Ошибка: {msg}")
                    self._update_response_height(f"Ошибка: {msg}")
                    # Retain user's query in entry so it is never lost on error
                    if self._last_prompt:
                        self.ent.delete(0, tk.END)
                        self.ent.insert(0, self._last_prompt)
                        self.ent.config(fg="#ffffff")
                        self._placeholder_active = False
                    self.controller.update_geometry()

                self._post_to_ui(on_error)

        threading.Thread(target=worker, daemon=True).start()

    def _copy_response(self) -> None:
        """Copies latest response to clipboard with minimalist visual feedback."""
        val = self.txt_res.get("1.0", tk.END).strip()
        if not val or val.startswith("Запрос к Gemini") or val.startswith("Подготовка ответа"):
            return

        try:
            self.card_frame.clipboard_clear()
            self.card_frame.clipboard_append(val)
            self.card_frame.update()
        except Exception:
            pass

        if self.sound_fn and self.settings and getattr(self.settings, "sound_enabled", True):
            self.sound_fn(True)

        self.lbl_notify.config(text="✓ Скопировано!", fg="#4ade80")
        if hasattr(self, "btn_copy") and isinstance(self.btn_copy, tk.Label):
            if hasattr(self.resp_box, "_copy_check"):
                self.btn_copy.config(image=self.resp_box._copy_check)
                self.card_frame.after(
                    1600,
                    lambda: self.btn_copy.config(image=self.resp_box._copy_norm)
                    if self.card_frame.winfo_exists()
                    else None,
                )
            else:
                self.btn_copy.config(text="✓", fg="#4ade80")
                self.card_frame.after(
                    1600,
                    lambda: self.btn_copy.config(text="❐", fg="#94a3b8")
                    if self.card_frame.winfo_exists()
                    else None,
                )
        self.card_frame.after(
            2000,
            lambda: self.lbl_notify.config(text="")
            if self.card_frame.winfo_exists()
            else None,
        )

    def _open_extended_chat(self) -> None:
        """Delegates opening the full multi-turn chat window continuing current session."""
        self.on_open_extended(self.session)


class AIExtendedChatWindow(tk.Toplevel):
    """Full-featured standalone multi-turn chat window for deep image analysis.
    Preserves exact session history, handles subsequent questions without re-uploading,
    and features styled chat cards, quick follow-up chips, and non-blocking streaming.
    """

    FOLLOW_UP_SUGGESTIONS = [
        "Объясни подробнее",
        "Приведи пример кода",
        "Как это исправить?",
        "Переведи на русский",
    ]

    def __init__(
        self,
        parent: tk.Misc,
        session: GeminiChatSession,
        crop_image: Optional[Image.Image] = None,
        db=None,
        settings=None,
        sound_fn: Optional[Callable[[bool], None]] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        super().__init__(parent)
        self.session = session
        self.crop_image = crop_image
        self.db = db
        self.settings = settings
        self.sound_fn = sound_fn
        self._logger = logger or logging.getLogger(__name__)

        self.title("ScreenLens — Расширенный AI Чат (Gemini Vision)")
        self.geometry("780x640")
        self.minsize(580, 460)
        self.config(bg="#181a1d")

        self.is_loading = False
        self._thumb_photo: Optional[ImageTk.PhotoImage] = None

        self._ui_queue: queue.Queue = queue.Queue()
        self._schedule_ui_queue()

        self._build_ui()
        self._render_history()

    def _schedule_ui_queue(self) -> None:
        """Polls UI queue on Tkinter main thread every 25ms."""
        def _poll():
            try:
                while True:
                    fn = self._ui_queue.get_nowait()
                    try:
                        fn()
                    except Exception as exc:
                        self._logger.exception("Chat UI queue task error: %s", exc)
            except queue.Empty:
                pass
            except Exception:
                pass
            try:
                if self.winfo_exists():
                    self.after(25, _poll)
            except Exception:
                pass

        try:
            self.after(25, _poll)
        except Exception:
            pass

    def _post_to_ui(self, fn: Callable[[], None]) -> None:
        """Dispatches callback from worker threads safely to the main thread."""
        self._ui_queue.put(fn)

    def _build_ui(self) -> None:
        """Constructs the extended chat layout."""
        # Top Header Bar
        header = tk.Frame(self, bg="#222428", padx=16, pady=10)
        header.pack(fill=tk.X)

        # Image Thumbnail
        thumb_src = self.crop_image if self.crop_image else self.session.image_path
        self._thumb_photo = create_rounded_thumbnail(thumb_src, target_size=(54, 54), radius=8)
        if self._thumb_photo:
            tk.Label(header, image=self._thumb_photo, bg="#222428", bd=0).pack(side=tk.LEFT, padx=(0, 12))

        # Title and info
        meta_col = tk.Frame(header, bg="#222428")
        meta_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tk.Label(
            meta_col,
            text="Мультимодальный диалог с Google Gemini",
            font=("Segoe UI", 11, "bold"),
            fg="#ffffff",
            bg="#222428",
        ).pack(anchor=tk.W)

        model_name = getattr(self.session.client, "_model", "gemini-3.5-flash-lite")
        info_text = f"Модель: {model_name} • Сессия: {self.session.session_id[-8:]}"
        if self.session.ocr_text:
            info_text += f" • OCR: {len(self.session.ocr_text)} симв."

        tk.Label(
            meta_col,
            text=info_text,
            font=("Segoe UI", 8),
            fg="#9aa0a6",
            bg="#222428",
        ).pack(anchor=tk.W, pady=(2, 0))

        # Header action buttons
        hdr_actions = tk.Frame(header, bg="#222428")
        hdr_actions.pack(side=tk.RIGHT)

        CapsuleButton(
            hdr_actions,
            text="Копировать весь диалог",
            style="secondary",
            card_bg="#222428",
            height=28,
            command=self._copy_full_dialog,
        ).pack(side=tk.LEFT, padx=(0, 6))

        CapsuleButton(
            hdr_actions,
            text="Закрыть",
            style="danger",
            card_bg="#222428",
            height=28,
            command=self.destroy,
        ).pack(side=tk.LEFT)

        # Main Conversation Scrollable Frame
        chat_container = tk.Frame(self, bg="#181a1d", padx=16, pady=8)
        chat_container.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(chat_container, bg="#181a1d", highlightthickness=0, bd=0)
        self.scrollbar = tk.Scrollbar(chat_container, orient=tk.VERTICAL, command=self.canvas.yview)
        self.scrollable_frame = tk.Frame(self.canvas, bg="#181a1d")

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self._canvas_win_id = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfig(self._canvas_win_id, width=e.width),
        )

        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Mousewheel scrolling
        def _on_mousewheel(event):
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        self.canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # Bottom Frame: Chips + Input Bar
        bottom_box = tk.Frame(self, bg="#222428", padx=16, pady=10)
        bottom_box.pack(fill=tk.X, side=tk.BOTTOM)

        # Chips row
        chips_f = tk.Frame(bottom_box, bg="#222428")
        chips_f.pack(fill=tk.X, pady=(0, 8))

        tk.Label(
            chips_f,
            text="Подсказки:",
            font=("Segoe UI", 8, "bold"),
            fg="#9aa0a6",
            bg="#222428",
        ).pack(side=tk.LEFT, padx=(0, 6))

        for text in self.FOLLOW_UP_SUGGESTIONS:
            b = CapsuleButton(
                chips_f,
                text=text,
                style="tonal",
                card_bg="#222428",
                height=24,
                command=lambda t=text: self._send_suggestion(t),
            )
            b.pack(side=tk.LEFT, padx=3)

        # Input box row
        inp_row = tk.Frame(bottom_box, bg="#181a1d", bd=1, relief=tk.SOLID)
        inp_row.pack(fill=tk.X)

        self.txt_input = tk.Text(
            inp_row,
            height=3,
            wrap=tk.WORD,
            bg="#181a1d",
            fg="#ffffff",
            insertbackground="#ffffff",
            selectbackground="#3b82f6",
            relief=tk.FLAT,
            bd=0,
            font=("Segoe UI", 9),
            padx=10,
            pady=8,
        )
        self.txt_input.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.txt_input.insert(tk.END, "Задайте вопрос (Enter — отправить, Shift+Enter — новая строка)...")
        self._input_placeholder = True

        def _on_in_focus(e):
            if self._input_placeholder:
                self.txt_input.delete("1.0", tk.END)
                self.txt_input.config(fg="#ffffff")
                self._input_placeholder = False

        def _on_in_blur(e):
            if not self.txt_input.get("1.0", tk.END).strip():
                self.txt_input.insert(tk.END, "Задайте вопрос (Enter — отправить, Shift+Enter — новая строка)...")
                self.txt_input.config(fg="#80868b")
                self._input_placeholder = True

        self.txt_input.bind("<FocusIn>", _on_in_focus)
        self.txt_input.bind("<FocusOut>", _on_in_blur)
        self.txt_input.bind("<Return>", self._on_enter_pressed)

        send_col = tk.Frame(inp_row, bg="#181a1d", padx=8, pady=8)
        send_col.pack(side=tk.RIGHT, fill=tk.Y)

        self.btn_chat_send = CapsuleButton(
            send_col,
            text="Отправить",
            style="primary",
            card_bg="#181a1d",
            height=30,
            command=self._send_chat_message,
        )
        self.btn_chat_send.pack(anchor=tk.CENTER)

        self.lbl_chat_status = tk.Label(
            bottom_box,
            text="",
            font=("Segoe UI", 8),
            fg="#4ade80",
            bg="#222428",
        )
        self.lbl_chat_status.pack(anchor=tk.W, pady=(4, 0))

    def _render_history(self) -> None:
        """Renders existing session messages as styled cards."""
        for w in self.scrollable_frame.winfo_children():
            w.destroy()

        if not self.session.history:
            empty_card = tk.Frame(self.scrollable_frame, bg="#222428", padx=16, pady=16)
            empty_card.pack(fill=tk.X, pady=12)
            tk.Label(
                empty_card,
                text="💬 Начало диалога",
                font=("Segoe UI", 10, "bold"),
                fg="#ffffff",
                bg="#222428",
            ).pack(anchor=tk.W)
            tk.Label(
                empty_card,
                text="Задайте любой вопрос по выделенному снимку экрана. Gemini видит изображение целиком и отвечает с учётом контекста.",
                font=("Segoe UI", 9),
                fg="#9aa0a6",
                bg="#222428",
                wraplength=650,
                justify=tk.LEFT,
            ).pack(anchor=tk.W, pady=(4, 0))
            return

        for msg in self.session.history:
            self._add_message_card(msg)

        self.canvas.update_idletasks()
        self.canvas.yview_moveto(1.0)

    def _add_message_card(self, msg: ChatMessage) -> None:
        """Appends a single styled chat bubble to the scrollable view."""
        is_user = (msg.role == "user")
        card_bg = "#252e3d" if is_user else "#1f2227"
        border_col = "#3b82f6" if is_user else "#36393e"

        bubble = tk.Frame(self.scrollable_frame, bg=card_bg, bd=1, relief=tk.SOLID, padx=12, pady=10)
        bubble.pack(fill=tk.X, pady=6, anchor=tk.E if is_user else tk.W)

        # Bubble Header
        b_hdr = tk.Frame(bubble, bg=card_bg)
        b_hdr.pack(fill=tk.X, pady=(0, 4))

        author = "Вы" if is_user else "Gemini 3.5 Flash Lite"
        tk.Label(
            b_hdr,
            text=author,
            font=("Segoe UI", 9, "bold"),
            fg="#60a5fa" if is_user else "#4ade80",
            bg=card_bg,
        ).pack(side=tk.LEFT)

        time_str = time.strftime("%H:%M:%S", time.localtime(msg.timestamp))
        tk.Label(
            b_hdr,
            text=time_str,
            font=("Segoe UI", 8),
            fg="#80868b",
            bg=card_bg,
        ).pack(side=tk.LEFT, padx=8)

        if not is_user:
            btn_copy = tk.Label(
                b_hdr,
                text="Скопировать",
                font=("Segoe UI", 8),
                fg="#9aa0a6",
                bg=card_bg,
                cursor="hand2",
            )
            btn_copy.pack(side=tk.RIGHT)
            btn_copy.bind("<Button-1>", lambda e, text=msg.text: self._copy_single_message(text))
            btn_copy.bind("<Enter>", lambda e, w=btn_copy: w.config(fg="#ffffff"))
            btn_copy.bind("<Leave>", lambda e, w=btn_copy: w.config(fg="#9aa0a6"))

        # Message Text
        msg_txt = tk.Text(
            bubble,
            wrap=tk.WORD,
            bg=card_bg,
            fg="#ffffff" if is_user else "#e2e8f0",
            font=("Segoe UI", 9),
            relief=tk.FLAT,
            bd=0,
            selectbackground="#3b82f6",
            highlightthickness=0,
            padx=0,
            pady=0,
        )
        msg_txt.insert(tk.END, msg.text)
        # Compute exact lines
        lines = len(msg.text.splitlines())
        msg_txt.config(height=max(1, min(lines, 30)), state="disabled")
        msg_txt.pack(fill=tk.X, expand=True)

    def _copy_single_message(self, text: str) -> None:
        """Copies text of an individual response."""
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update()
        except Exception:
            pass
        if self.sound_fn and self.settings and getattr(self.settings, "sound_enabled", True):
            self.sound_fn(True)
        self.lbl_chat_status.config(text="✓ Ответ скопирован в буфер обмена!")
        self.after(2000, lambda: self.lbl_chat_status.config(text=""))

    def _copy_full_dialog(self) -> None:
        """Copies the entire conversation transcript formatted cleanly."""
        if not self.session.history:
            return
        lines = [f"=== ScreenLens AI Диалог ({self.session.session_id}) ===\n"]
        for msg in self.session.history:
            author = "Пользователь" if msg.role == "user" else "Gemini"
            t = time.strftime("%H:%M:%S", time.localtime(msg.timestamp))
            lines.append(f"[{t}] {author}:\n{msg.text}\n")
        full_dialog = "\n".join(lines)

        try:
            self.clipboard_clear()
            self.clipboard_append(full_dialog)
            self.update()
        except Exception:
            pass
        if self.sound_fn and self.settings and getattr(self.settings, "sound_enabled", True):
            self.sound_fn(True)
        self.lbl_chat_status.config(text="✓ Полный диалог скопирован!")
        self.after(2000, lambda: self.lbl_chat_status.config(text=""))

    def _on_enter_pressed(self, event) -> Optional[str]:
        """Handles Enter key: Send on Enter, newline on Shift+Enter."""
        if event.state & 1:  # Shift pressed
            return None
        self._send_chat_message()
        return "break"

    def _send_suggestion(self, text: str) -> None:
        """Populates and immediately sends a suggested follow-up."""
        if self.is_loading:
            return
        self.txt_input.delete("1.0", tk.END)
        self.txt_input.insert(tk.END, text)
        self._input_placeholder = False
        self._send_chat_message()

    def _send_chat_message(self) -> None:
        """Submits message in the ongoing multi-turn session."""
        if self.is_loading:
            return

        text = self.txt_input.get("1.0", tk.END).strip()
        if self._input_placeholder or not text or text.startswith("Задайте вопрос"):
            return

        self.txt_input.delete("1.0", tk.END)
        self.is_loading = True
        self.btn_chat_send.config(state="disabled")
        self.lbl_chat_status.config(text="● Gemini думает...", fg="#60a5fa")

        # Temporarily append user bubble immediately for high responsiveness
        user_msg = ChatMessage(role="user", text=text)
        self._add_message_card(user_msg)
        self.canvas.update_idletasks()
        self.canvas.yview_moveto(1.0)

        def worker():
            try:
                ans = self.session.send_message(text)

                def on_success(res_text=ans):
                    self.is_loading = False
                    self.btn_chat_send.config(state="normal")
                    self.lbl_chat_status.config(text="✓ Ответ получен", fg="#4ade80")
                    self.after(2000, lambda: self.lbl_chat_status.config(text=""))

                    # Re-render complete history to ensure perfect consistency
                    self._render_history()

                    if self.db:
                        self.db.add_history(
                            image_path=str(self.session.image_path),
                            ocr_text=self.session.ocr_text,
                            gemini_result=res_text,
                        )

                self._post_to_ui(on_success)
            except Exception as exc:
                err_msg = str(exc)
                self._logger.exception("Chat session send error: %s", err_msg)

                def on_error(msg=err_msg):
                    self.is_loading = False
                    self.btn_chat_send.config(state="normal")
                    self.lbl_chat_status.config(text=f"Ошибка: {msg}", fg="#f87171")
                    # Restore text in input field so user does not lose it
                    self.txt_input.delete("1.0", tk.END)
                    self.txt_input.insert(tk.END, text)
                    self._input_placeholder = False
                    # Remove the unfulfilled user bubble by re-rendering
                    self._render_history()

                self._post_to_ui(on_error)

        threading.Thread(target=worker, daemon=True).start()
