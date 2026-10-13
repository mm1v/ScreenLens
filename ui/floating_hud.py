import math
import threading
import tkinter as tk
from tkinter import ttk
from pathlib import Path
from typing import Callable, Optional

from PIL import Image, ImageDraw, ImageTk
from core.actions import open_browser_url, open_in_google_lens, translate_text
from core.clipboard import ClipboardManager
from core.gemini import GeminiVisionClient


def get_hud_placement(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    hud_w: int = 442,
    hud_h: int = 58,
    screen_w: int = 1920,
    screen_h: int = 1080,
    margin: int = 14,
) -> tuple[int, int]:
    """Calculates anchored HUD position: centered below selection if room, else above; clamped to screen."""
    # 1. Horizontal: strictly centered relative to selection
    hx = x1 + ((x2 - x1) - hud_w) // 2
    hx = max(margin, min(screen_w - hud_w - margin, hx))

    # 2. Vertical: prefer below selection with clean gap
    hy = y2 + margin
    if hy + hud_h + margin > screen_h:
        # Not enough room below -> place above selection
        hy = y1 - hud_h - margin
        if hy < margin:
            # Huge selection occupying screen -> place inside at bottom
            hy = max(margin, min(screen_h - hud_h - margin, y2 - hud_h - margin))

    return hx, hy


def get_card_placement(
    box: tuple[int, int, int, int],
    card_w: int,
    card_h: int,
    hud_x: int,
    hud_y: int,
    hud_w: int = 442,
    hud_h: int = 58,
    screen_w: int = 1920,
    screen_h: int = 1080,
    margin: int = 14,
    gap: int = 14,
) -> tuple[int, int]:
    """Calculates safe placement for result card with guaranteed offset from the HUD bar (no overlap)."""
    bx1, by1, bx2, by2 = box

    # 1. Center horizontally relative to selection, clamped to margins
    cx = bx1 + ((bx2 - bx1) - card_w) // 2
    cx = max(margin, min(screen_w - card_w - margin, cx))

    # 2. Vertical: never overlap the HUD bar
    hud_is_below = hud_y >= by2
    if hud_is_below:
        # HUD is below selection. Try placing card directly below the HUD bar:
        cy = hud_y + hud_h + gap
        if cy + card_h + margin <= screen_h:
            return cx, cy

        # Not enough room below HUD -> place card above the selection box:
        cy = by1 - card_h - gap
        if cy >= margin:
            return cx, cy

        # Extremely tall selection: place card above HUD or clamped
        cy = max(margin, min(screen_h - card_h - margin, hud_y - card_h - gap))
        return cx, cy
    else:
        # HUD is above selection. Try placing card directly above HUD:
        cy = hud_y - card_h - gap
        if cy >= margin:
            return cx, cy

        # Not enough room above HUD -> place card below the selection box:
        cy = by2 + gap
        if cy + card_h + margin <= screen_h:
            return cx, cy

        # Extremely tall selection: clamp below selection
        cy = max(margin, min(screen_h - card_h - margin, by2 + gap))
        return cx, cy


def render_monochrome_icons() -> dict[str, Image.Image]:
    """Generates crisp, anti-aliased monochrome vector icons in pure white/red line art."""
    scale = 4
    size = 20 * scale
    icons: dict[str, Image.Image] = {}

    # 1. Text (Document lines)
    im_txt = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im_txt)
    tx1, ty1, tx2, ty2 = 4 * scale, 2.5 * scale, 16 * scale, 17.5 * scale
    d.rounded_rectangle([tx1, ty1, tx2, ty2], radius=2 * scale, outline=(255, 255, 255, 255), width=int(1.8 * scale))
    d.line([6.5 * scale, 6.5 * scale, 13.5 * scale, 6.5 * scale], fill=(255, 255, 255, 255), width=int(1.6 * scale))
    d.line([6.5 * scale, 10 * scale, 13.5 * scale, 10 * scale], fill=(255, 255, 255, 255), width=int(1.6 * scale))
    d.line([6.5 * scale, 13.5 * scale, 11 * scale, 13.5 * scale], fill=(255, 255, 255, 255), width=int(1.6 * scale))
    icons["text"] = im_txt.resize((20, 20), Image.Resampling.LANCZOS)

    # 2. Translate (文A)
    im_tr = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im_tr)
    lw = int(1.7 * scale)
    d.line([7 * scale, 2.8 * scale, 7 * scale, 5 * scale], fill=(255, 255, 255, 255), width=lw)
    d.line([2.5 * scale, 5.2 * scale, 11 * scale, 5.2 * scale], fill=(255, 255, 255, 255), width=lw)
    d.line([7.2 * scale, 5.2 * scale, 2 * scale, 14 * scale], fill=(255, 255, 255, 255), width=lw)
    d.line([6 * scale, 6.8 * scale, 11.5 * scale, 14 * scale], fill=(255, 255, 255, 255), width=lw)
    ax, ay, aw, ah = 10.5 * scale, 6.8 * scale, 6.8 * scale, 9.2 * scale
    d.line([ax, ay + ah, ax + aw / 2, ay], fill=(255, 255, 255, 255), width=lw)
    d.line([ax + aw / 2, ay, ax + aw, ay + ah], fill=(255, 255, 255, 255), width=lw)
    d.line([ax + aw * 0.25, ay + ah * 0.62, ax + aw * 0.75, ay + ah * 0.62], fill=(255, 255, 255, 255), width=lw)
    icons["translate"] = im_tr.resize((20, 20), Image.Resampling.LANCZOS)

    # 3. Lens (Поиск) with Windows shortcut / open-in-window indicator
    im_lens = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im_lens)
    # Search loupe slightly to the bottom-left
    cx, cy = 7.5 * scale, 9.5 * scale
    r = 4.2 * scale
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 255, 255, 255), width=int(1.8 * scale))
    hx1 = cx + (r - 0.5 * scale) * 0.7071
    hy1 = cy + (r - 0.5 * scale) * 0.7071
    d.line([hx1, hy1, hx1 + 4.2 * scale, hy1 + 4.2 * scale], fill=(255, 255, 255, 255), width=int(2.2 * scale))

    # Windows shortcut / external window badge in top-right
    # Mini window frame
    d.line([12 * scale, 5 * scale, 12 * scale, 9 * scale], fill=(255, 255, 255, 255), width=int(1.3 * scale))
    d.line([12 * scale, 9 * scale, 16 * scale, 9 * scale], fill=(255, 255, 255, 255), width=int(1.3 * scale))
    d.line([16 * scale, 9 * scale, 16 * scale, 7 * scale], fill=(255, 255, 255, 255), width=int(1.3 * scale))
    # Shortcut diagonal arrow pointing up-right
    d.line([13.5 * scale, 6.5 * scale, 17.5 * scale, 2.5 * scale], fill=(255, 255, 255, 255), width=int(1.6 * scale))
    d.line([14.8 * scale, 2.5 * scale, 17.5 * scale, 2.5 * scale], fill=(255, 255, 255, 255), width=int(1.6 * scale))
    d.line([17.5 * scale, 2.5 * scale, 17.5 * scale, 5.2 * scale], fill=(255, 255, 255, 255), width=int(1.6 * scale))
    icons["lens"] = im_lens.resize((20, 20), Image.Resampling.LANCZOS)

    # 4. AI (Gemini sparkle)
    im_ai = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im_ai)
    scx, scy, sr = 9 * scale, 10 * scale, 7.5 * scale
    pts = [
        (scx, scy - sr), (scx + 1.8 * scale, scy - 1.8 * scale),
        (scx + sr, scy), (scx + 1.8 * scale, scy + 1.8 * scale),
        (scx, scy + sr), (scx - 1.8 * scale, scy + 1.8 * scale),
        (scx - sr, scy), (scx - 1.8 * scale, scy - 1.8 * scale),
    ]
    d.polygon(pts, fill=(255, 255, 255, 255))
    sat_cx, sat_cy, sat_r = 15.5 * scale, 4.5 * scale, 3 * scale
    s_pts = [
        (sat_cx, sat_cy - sat_r), (sat_cx + 0.8 * scale, sat_cy - 0.8 * scale),
        (sat_cx + sat_r, sat_cy), (sat_cx + 0.8 * scale, sat_cy + 0.8 * scale),
        (sat_cx, sat_cy + sat_r), (sat_cx - 0.8 * scale, sat_cy - 0.8 * scale),
        (sat_cx - sat_r, sat_cy), (sat_cx - 0.8 * scale, sat_cy - 0.8 * scale),
    ]
    d.polygon(s_pts, fill=(255, 255, 255, 255))
    icons["ai"] = im_ai.resize((20, 20), Image.Resampling.LANCZOS)

    # 5. Copy (Копировать)
    im_copy = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im_copy)
    d.rounded_rectangle([6.5 * scale, 2.5 * scale, 16.5 * scale, 13.5 * scale], radius=2 * scale, outline=(255, 255, 255, 255), width=int(1.6 * scale))
    d.rounded_rectangle([3 * scale, 6 * scale, 13 * scale, 17 * scale], radius=2 * scale, fill=(42, 44, 48, 255), outline=(255, 255, 255, 255), width=int(1.6 * scale))
    icons["copy"] = im_copy.resize((20, 20), Image.Resampling.LANCZOS)

    # 6. Close (✕) — DANGER RED
    im_close = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im_close)
    cx, cy = 10 * scale, 10 * scale
    cr = 4.5 * scale
    cw = int(2.2 * scale)
    red_color = (255, 82, 82, 255)
    d.line([cx - cr, cy - cr, cx + cr, cy + cr], fill=red_color, width=cw)
    d.line([cx + cr, cy - cr, cx - cr, cy + cr], fill=red_color, width=cw)
    icons["close"] = im_close.resize((20, 20), Image.Resampling.LANCZOS)

    return icons


def render_lens_corners(size: int = 44, radius: int = 16, leg: int = 34, thickness: int = 3) -> dict[str, Image.Image]:
    """Generates 4 perfectly smooth, anti-aliased corner sprites (x4 supersampling downscaled with Lanczos)."""
    scale = 4
    s = size * scale
    r = radius * scale
    l = leg * scale
    th = int(thickness * scale)
    pad = int((thickness + 2) * scale // 2)

    corners: dict[str, Image.Image] = {}
    for c_name in ("nw", "ne", "sw", "se"):
        im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)

        # 1. Soft dark shadow pass for contrast on any background
        # 2. Crisp pure white pass
        for (w, col) in [(th + int(2 * scale), (10, 12, 16, 170)), (th, (255, 255, 255, 255))]:
            if c_name == "nw":
                d.arc([pad, pad, pad + 2 * r, pad + 2 * r], start=180, end=270, fill=col, width=w)
                d.line([pad + r, pad, pad + l, pad], fill=col, width=w)
                d.line([pad, pad + r, pad, pad + l], fill=col, width=w)
            elif c_name == "ne":
                d.arc([s - pad - 2 * r, pad, s - pad, pad + 2 * r], start=270, end=360, fill=col, width=w)
                d.line([s - pad - l, pad, s - pad - r, pad], fill=col, width=w)
                d.line([s - pad, pad + r, s - pad, pad + l], fill=col, width=w)
            elif c_name == "sw":
                d.arc([pad, s - pad - 2 * r, pad + 2 * r, s - pad], start=90, end=180, fill=col, width=w)
                d.line([pad + r, s - pad, pad + l, s - pad], fill=col, width=w)
                d.line([pad, pad + r, pad, pad + l], fill=col, width=w)
            elif c_name == "se":
                d.arc([s - pad - 2 * r, s - pad - 2 * r, s - pad, s - pad], start=0, end=90, fill=col, width=w)
                d.line([s - pad - l, s - pad, s - pad - r, s - pad], fill=col, width=w)
                d.line([s - pad, s - pad - l, s - pad, s - pad - r], fill=col, width=w)

        corners[c_name] = im.resize((size, size), Image.Resampling.LANCZOS)
    return corners


def render_capsule_image(
    w: int,
    h: int,
    fill: tuple[int, int, int, int] = (64, 66, 71, 248),
    outline: tuple[int, int, int, int] = (82, 85, 92, 255),
    outline_width: float = 1.0,
) -> Image.Image:
    """Generates an anti-aliased stadium capsule image (border-radius: 9999px) with 4x supersampling."""
    scale = 4
    sw, sh = w * scale, h * scale
    im = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    r = sh // 2
    pad = int(outline_width * scale)
    d.rounded_rectangle(
        [pad, pad, sw - pad - 1, sh - pad - 1],
        radius=r,
        fill=fill,
        outline=outline,
        width=pad,
    )
    return im.resize((w, h), Image.Resampling.LANCZOS)


def render_rounded_rect_image(
    w: int,
    h: int,
    radius: int = 22,
    fill: tuple[int, int, int, int] = (34, 36, 40, 255),
    outline: tuple[int, int, int, int] = (70, 74, 82, 255),
    outline_width: float = 1.5,
) -> Image.Image:
    """Generates an anti-aliased rounded rectangle image with 4x supersampling."""
    scale = 4
    sw, sh = w * scale, h * scale
    r = radius * scale
    im = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    pad = int(outline_width * scale)
    d.rounded_rectangle(
        [pad, pad, sw - pad - 1, sh - pad - 1],
        radius=r,
        fill=fill,
        outline=outline,
        width=pad,
    )
    return im.resize((w, h), Image.Resampling.LANCZOS)


def render_pill_badge(text: str, h: int = 22) -> tuple[ImageTk.PhotoImage, int, int]:
    """Generates a compact anti-aliased stadium capsule badge PhotoImage for dimension display."""
    char_w = 7
    w = max(50, len(text) * char_w + 16)
    img = render_capsule_image(
        w,
        h,
        fill=(26, 28, 30, 240),
        outline=(62, 65, 71, 255),
        outline_width=1.0,
    )
    return ImageTk.PhotoImage(img), w, h


def calc_pill_width(text: str, min_w: int = 84, pad: int = 28, font_spec: tuple = ("Segoe UI", 9, "bold")) -> int:
    """Calculates generous, anti-clipping width for stadium pill button using font metrics."""
    try:
        from tkinter import font as tkfont
        f = tkfont.Font(family=font_spec[0], size=font_spec[1], weight=font_spec[2] if len(font_spec) > 2 else "normal")
        measured = f.measure(text)
    except Exception:
        measured = int(len(text) * 8.0)
    return max(min_w, measured + pad)


def create_rounded_card(
    canvas: tk.Canvas,
    cx: int,
    cy: int,
    card_w: int,
    card_h: int,
    radius: int = 22,
    bg_color: tuple[int, int, int, int] = (34, 36, 40, 255),
    outline_color: tuple[int, int, int, int] = (70, 74, 82, 255),
    bg_hex: str = "#222428",
    pad_x: int = 18,
    pad_y: int = 14,
) -> tuple[tk.Frame, ImageTk.PhotoImage]:
    """Renders a smooth, anti-aliased rounded card directly on the overlay Canvas."""
    bg_img = render_rounded_rect_image(
        card_w, card_h, radius=radius, fill=bg_color, outline=outline_color, outline_width=1.5
    )
    bg_photo = ImageTk.PhotoImage(bg_img)
    canvas.create_image(cx, cy, image=bg_photo, anchor=tk.NW, tags="active_card")

    content_frame = tk.Frame(canvas, bg=bg_hex, bd=0)
    canvas.create_window(
        cx + pad_x,
        cy + pad_y,
        window=content_frame,
        width=card_w - 2 * pad_x,
        height=card_h - 2 * pad_y,
        anchor=tk.NW,
        tags="active_card",
    )
    return content_frame, bg_photo


class RoundedCardController:
    """Dynamic auto-sizing controller for canvas-rendered rounded result cards.
    Prevents button clipping by adapting card geometry to actual content requirements.
    Initially positions card at calculated placement in hidden state to eliminate top-left corner flicker.
    """

    def __init__(
        self,
        canvas: tk.Canvas,
        box: tuple[int, int, int, int],
        hud_pos: tuple[int, int, int, int],
        screen_size: tuple[int, int] = (1920, 1080),
        min_w: int = 480,
        max_w: int = 580,
        pad_x: int = 18,
        pad_y: int = 14,
        radius: int = 22,
        bg_color: tuple[int, int, int, int] = (34, 36, 40, 255),
        outline_color: tuple[int, int, int, int] = (70, 74, 82, 255),
        bg_hex: str = "#222428",
    ) -> None:
        self.canvas = canvas
        self.box = box
        self.hud_pos = hud_pos
        self.screen_w, self.screen_h = screen_size
        self.min_w = min_w
        self.max_w = max_w
        self.pad_x = pad_x
        self.pad_y = pad_y
        self.radius = radius
        self.bg_color = bg_color
        self.outline_color = outline_color
        self.bg_hex = bg_hex

        # Pre-calculate estimated placement immediately below the HUD (NEVER at 0, 0!)
        self.card_w = min_w
        self.card_h = 200
        hx, hy, hw, hh = self.hud_pos
        self.cx, self.cy = get_card_placement(
            self.box,
            self.card_w,
            self.card_h,
            hx,
            hy,
            hw,
            hh,
            self.screen_w,
            self.screen_h,
            margin=14,
            gap=14,
        )
        self.bg_photo: Optional[ImageTk.PhotoImage] = None

        # Content container initially created at destination coordinates in HIDDEN state
        self.content_frame = tk.Frame(canvas, bg=bg_hex, bd=0)
        self.bg_id = canvas.create_image(self.cx, self.cy, anchor=tk.NW, tags="active_card", state="hidden")
        self.win_id = canvas.create_window(
            self.cx + self.pad_x,
            self.cy + self.pad_y,
            window=self.content_frame,
            anchor=tk.NW,
            tags="active_card",
            state="hidden",
        )

    def update_geometry(self) -> None:
        """Measures actual content reqheight/reqwidth, adapts card geometry, and seamlessly reveals it."""
        self.content_frame.update_idletasks()
        req_w = self.content_frame.winfo_reqwidth()
        req_h = self.content_frame.winfo_reqheight()

        actual_w = max(self.min_w, min(self.max_w, req_w + 2 * self.pad_x))
        actual_h = max(100, req_h + 2 * self.pad_y)

        self.card_w = actual_w
        self.card_h = actual_h

        hx, hy, hw, hh = self.hud_pos
        self.cx, self.cy = get_card_placement(
            self.box,
            self.card_w,
            self.card_h,
            hx,
            hy,
            hw,
            hh,
            self.screen_w,
            self.screen_h,
            margin=14,
            gap=14,
        )

        bg_img = render_rounded_rect_image(
            self.card_w,
            self.card_h,
            radius=self.radius,
            fill=self.bg_color,
            outline=self.outline_color,
            outline_width=1.5,
        )
        self.bg_photo = ImageTk.PhotoImage(bg_img)
        self.canvas._active_card_photo = self.bg_photo

        self.canvas.coords(self.bg_id, self.cx, self.cy)
        self.canvas.itemconfig(self.bg_id, image=self.bg_photo)

        inner_w = self.card_w - 2 * self.pad_x
        inner_h = self.card_h - 2 * self.pad_y
        self.canvas.coords(self.win_id, self.cx + self.pad_x, self.cy + self.pad_y)
        self.canvas.itemconfigure(self.win_id, width=inner_w, height=inner_h)

        # Reveal at final placement with zero flicker
        self.canvas.itemconfigure(self.bg_id, state="normal")
        self.canvas.itemconfigure(self.win_id, state="normal")

    def destroy(self) -> None:
        """Removes card and cleans up canvas objects."""
        self.canvas.delete("active_card")
        self.bg_photo = None


class CapsuleButton(tk.Canvas):
    """Material 3 Expressive stadium capsule button (shape.corner.full) with 4x supersampled anti-aliasing."""

    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        command: Optional[Callable[[], None]] = None,
        style: str = "primary",  # "primary", "secondary", "tonal", "danger", "success"
        card_bg: str = "#222428",
        width: Optional[int] = None,
        height: int = 32,
        font: tuple = ("Segoe UI", 9, "bold"),
        **kwargs,
    ) -> None:
        if width is None:
            width = calc_pill_width(text, min_w=84, pad=28, font_spec=font)

        super().__init__(
            parent,
            width=width,
            height=height,
            bg=card_bg,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
            **kwargs,
        )
        self.command = command
        self.text_label = text
        self.w = width
        self.h = height
        self.font = font
        self.style = style
        self._is_pressed = False

        # Material 3 Expressive color tokens
        if style == "primary":
            fill_norm = (255, 255, 255, 255)
            fill_hov = (226, 230, 238, 255)
            fill_press = (200, 206, 218, 255)
            out_norm = (255, 255, 255, 255)
            fg_norm = "#121212"
        elif style == "danger":
            fill_norm = (60, 26, 26, 255)
            fill_hov = (82, 32, 32, 255)
            fill_press = (46, 20, 20, 255)
            out_norm = (145, 42, 42, 255)
            fg_norm = "#ff5252"
        elif style == "success":
            fill_norm = (22, 58, 38, 255)
            fill_hov = (30, 78, 50, 255)
            fill_press = (16, 46, 30, 255)
            out_norm = (40, 140, 80, 255)
            fg_norm = "#00e676"
        else:  # secondary / tonal
            fill_norm = (50, 53, 60, 255)
            fill_hov = (64, 68, 77, 255)
            fill_press = (40, 43, 49, 255)
            out_norm = (75, 80, 90, 255)
            fg_norm = "#ffffff"

        self._img_normal = ImageTk.PhotoImage(render_capsule_image(width, height, fill_norm, out_norm, outline_width=1.0))
        self._img_hover = ImageTk.PhotoImage(render_capsule_image(width, height, fill_hov, out_norm, outline_width=1.0))
        self._img_pressed = ImageTk.PhotoImage(render_capsule_image(width, height, fill_press, out_norm, outline_width=1.0))

        self.img_id = self.create_image(0, 0, image=self._img_normal, anchor=tk.NW)
        self.txt_id = self.create_text(width // 2, height // 2, text=text, fill=fg_norm, font=font)

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

        self.tag_bind(self.img_id, "<Enter>", self._on_enter)
        self.tag_bind(self.img_id, "<Leave>", self._on_leave)
        self.tag_bind(self.img_id, "<ButtonPress-1>", self._on_press)
        self.tag_bind(self.img_id, "<ButtonRelease-1>", self._on_release)

        self.tag_bind(self.txt_id, "<Enter>", self._on_enter)
        self.tag_bind(self.txt_id, "<Leave>", self._on_leave)
        self.tag_bind(self.txt_id, "<ButtonPress-1>", self._on_press)
        self.tag_bind(self.txt_id, "<ButtonRelease-1>", self._on_release)

    def _on_enter(self, _event: Optional[tk.Event] = None) -> None:
        if not self._is_pressed:
            self.itemconfig(self.img_id, image=self._img_hover)

    def _on_leave(self, _event: Optional[tk.Event] = None) -> None:
        self._is_pressed = False
        self.itemconfig(self.img_id, image=self._img_normal)

    def _on_press(self, _event: Optional[tk.Event] = None) -> None:
        self._is_pressed = True
        self.itemconfig(self.img_id, image=self._img_pressed)

    def _on_click(self, _event: Optional[tk.Event] = None) -> None:
        if self.command:
            self.command()

    def _on_release(self, event: Optional[tk.Event] = None) -> None:
        if self._is_pressed:
            self._is_pressed = False
            self.itemconfig(self.img_id, image=self._img_hover)
            self._on_click(event)

    def set_text(self, new_text: str) -> None:
        self.text_label = new_text
        self.itemconfig(self.txt_id, text=new_text)

    def config(self, **kwargs) -> None:
        if "text" in kwargs:
            self.set_text(kwargs.pop("text"))
        if kwargs:
            super().config(**kwargs)

    def configure(self, **kwargs) -> None:
        self.config(**kwargs)


class RoundedCard(tk.Canvas):
    """Backwards-compatible card container wrapper."""

    def __init__(
        self,
        parent: tk.Misc,
        width: int = 480,
        height: int = 220,
        bg_parent: str = "#141619",
        radius: int = 22,
        **kwargs,
    ) -> None:
        super().__init__(
            parent,
            width=width,
            height=height,
            bg=bg_parent,
            highlightthickness=0,
            bd=0,
            **kwargs,
        )
        self.w = width
        self.h = height
        self.radius = radius

        self._bg_img = render_rounded_rect_image(
            width,
            height,
            radius=radius,
            fill=(34, 36, 40, 255),
            outline=(70, 74, 82, 255),
            outline_width=1.5,
        )
        self._bg_photo = ImageTk.PhotoImage(self._bg_img)
        self.create_image(0, 0, image=self._bg_photo, anchor=tk.NW)

        self.content_frame = tk.Frame(self, bg="#222428", bd=0)
        self.create_window(18, 14, window=self.content_frame, anchor=tk.NW, width=width - 36, height=height - 28)


class HUDButtonInfo:
    """Descriptor for an interactive button in GoogleLensHUD."""

    def __init__(self, key: str, label: str, cx: int, cy: int, width: int = 60, height: int = 50) -> None:
        self.key = key
        self.label = label
        self.cx = cx
        self.cy = cy
        self.width = width
        self.height = height


class GoogleLensHUD:
    """Material Design 3 / Google Lens dual capsule HUD with generous padding and active mode accent pill."""

    def __init__(
        self,
        parent: Optional[tk.Canvas] = None,
        on_text: Optional[Callable[[], None]] = None,
        on_translate: Optional[Callable[[], None]] = None,
        on_lens: Optional[Callable[[], None]] = None,
        on_ai: Optional[Callable[[], None]] = None,
        on_copy: Optional[Callable[[], None]] = None,
        on_close: Optional[Callable[[], None]] = None,
        ai_enabled: bool = True,
        canvas: Optional[tk.Canvas] = None,
        root: Optional[tk.Misc] = None,
        **kwargs,
    ) -> None:
        self.canvas = parent if parent is not None else canvas
        self.root = root
        self.on_text = on_text or (lambda: None)
        self.on_translate = on_translate or (lambda: None)
        self.on_lens = on_lens or (lambda: None)
        self.on_ai = on_ai or (lambda: None)
        self.on_copy = on_copy or (lambda: None)
        self.on_close = on_close or (lambda: None)
        self.ai_enabled = ai_enabled

        # Configure Block 1 items depending on whether AI mode is enabled in settings
        self.b1_items: list[tuple[str, str]] = [
            ("text", "Текст"),
            ("translate", "Перевод"),
        ]
        if self.ai_enabled:
            self.b1_items.append(("ai", "ИИ"))
        self.b1_items.append(("lens", "Поиск"))

        # Compact edge paddings and sleek item widths
        self.side_pad = 8
        self.w_b1_item = 64
        self.b1_w = self.side_pad + len(self.b1_items) * self.w_b1_item + self.side_pad
        self.b1_h = 58

        self.w_copy = 76
        self.w_close = 68
        self.b2_w = self.side_pad + self.w_copy + self.w_close + self.side_pad  # exactly 160px
        self.b2_h = 58
        self.gap = 10

        self.x = 0
        self.y = 0
        self.is_visible = False
        self._hovered_btn: Optional[str] = None
        self.active_mode: Optional[str] = None  # Neutral on start; set only when user activates an action

        # Pre-render monochrome vector icons
        self._pil_icons = render_monochrome_icons()
        self._photo_icons: dict[str, ImageTk.PhotoImage] = {
            k: ImageTk.PhotoImage(v) for k, v in self._pil_icons.items()
        }

        # Medium gray outer capsule background (Google Lens reference)
        cap_fill = (44, 46, 52, 248)
        cap_outline = (72, 75, 84, 255)
        cap1_img = render_capsule_image(self.b1_w, self.b1_h, fill=cap_fill, outline=cap_outline, outline_width=1.0)
        cap2_img = render_capsule_image(self.b2_w, self.b2_h, fill=cap_fill, outline=cap_outline, outline_width=1.0)
        self._cap1_photo = ImageTk.PhotoImage(cap1_img)
        self._cap2_photo = ImageTk.PhotoImage(cap2_img)

        # Material 3 Expressive convex stadium capsule configurations per item
        # Stadium capsules (shape.corner.full) with radius = height // 2, matching outer block curvature
        self._act_pill_photos: dict[str, ImageTk.PhotoImage] = {}
        self._hov_pill_photos: dict[str, ImageTk.PhotoImage] = {}

        # Colors according to Material 3 Expressive Dark
        act_fill = (26, 28, 32, 255)          # High-contrast active container (charcoal)
        act_outline = (96, 102, 116, 255)     # M3 active border
        hov_fill = (54, 57, 65, 210)          # State layer hover
        hov_outline = (80, 85, 96, 255)
        danger_hov_fill = (75, 28, 28, 235)   # M3 error container hover
        danger_hov_outline = (150, 48, 48, 255)

        # Pill dimensions per button key: (width, height=50)
        # Stadium capsules matching outer container curvature and with 4px equal padding to the outer edges
        pill_dims = {
            "text": (64, 50),
            "translate": (62, 50),
            "ai": (56, 50),
            "lens": (64, 50),
            "copy": (74, 50),
            "close": (70, 50),
        }

        for k, (pw, ph) in pill_dims.items():
            act_im = render_capsule_image(pw, ph, fill=act_fill, outline=act_outline, outline_width=1.0)
            self._act_pill_photos[k] = ImageTk.PhotoImage(act_im)

            if k == "close":
                hov_im = render_capsule_image(pw, ph, fill=danger_hov_fill, outline=danger_hov_outline, outline_width=1.0)
            else:
                hov_im = render_capsule_image(pw, ph, fill=hov_fill, outline=hov_outline, outline_width=1.0)
            self._hov_pill_photos[k] = ImageTk.PhotoImage(hov_im)

        # Backwards-compatible single photo attributes
        self._act_pill_photo = self._act_pill_photos.get("translate", next(iter(self._act_pill_photos.values())))
        self._hov_norm_photo = self._hov_pill_photos.get("text", next(iter(self._hov_pill_photos.values())))
        self._hov_danger_photo = self._hov_pill_photos.get("close", next(iter(self._hov_pill_photos.values())))

        self.buttons: list[HUDButtonInfo] = []
        self._update_buttons_list()

    def _get_item_layout(self, key: str) -> tuple[int, int]:
        """Returns horizontal center offset from block start and pill width for a given button key."""
        if len(self.b1_items) == 4:
            layout_b1 = {
                "text": (36, 64),
                "translate": (105, 62),
                "ai": (170, 56),
                "lens": (236, 64),
            }
        else:
            layout_b1 = {
                "text": (36, 64),
                "translate": (104, 62),
                "lens": (172, 64),
            }
        layout_b2 = {
            "copy": (41, 74),
            "close": (121, 70),
        }
        if key in layout_b1:
            return layout_b1[key]
        return layout_b2.get(key, (36, 60))

    def _update_buttons_list(self) -> None:
        """Updates list of HUDButtonInfo descriptors with absolute button centers."""
        btns: list[HUDButtonInfo] = []
        cy1 = self.y + self.b1_h // 2
        for key, label in self.b1_items:
            cx_off, pw = self._get_item_layout(key)
            btns.append(HUDButtonInfo(key, label, self.x + cx_off, cy1, width=pw, height=50))

        bx2 = self.x + self.b1_w + self.gap
        cy2 = self.y + self.b2_h // 2
        cx_off_copy, pw_copy = self._get_item_layout("copy")
        btns.append(HUDButtonInfo("copy", "Копировать", bx2 + cx_off_copy, cy2, width=pw_copy, height=50))
        cx_off_close, pw_close = self._get_item_layout("close")
        btns.append(HUDButtonInfo("close", "Закрыть", bx2 + cx_off_close, cy2, width=pw_close, height=50))
        self.buttons = btns

    def get_dimensions(self) -> tuple[int, int]:
        """Returns total width and height of the dual capsule HUD."""
        return self.b1_w + self.gap + self.b2_w, self.b1_h

    def set_active_mode(self, mode_key: Optional[str]) -> None:
        """Sets the currently active mode and redraws to highlight it with the accent pill."""
        valid_keys = [k for k, _ in self.b1_items]
        if mode_key is None or mode_key in valid_keys:
            self.active_mode = mode_key
            if self.is_visible:
                self.draw(self.x, self.y)

    def reset_active_mode(self) -> None:
        """Resets active mode highlight to neutral so no button is accented initially."""
        self.active_mode = None
        if self.is_visible:
            self.draw(self.x, self.y)

    def place_at(self, x: int, y: int) -> None:
        """Places HUD at given coordinates (alias for draw)."""
        self.draw(x, y)

    def show(self) -> None:
        """Shows HUD at current coordinates."""
        if not self.is_visible:
            self.draw(self.x, self.y)

    def draw(self, x: int, y: int) -> None:
        """Renders or moves the two side-by-side capsules on the Canvas."""
        self.x = x
        self.y = y
        self.canvas.delete("lens_hud")
        self.canvas.delete("lens_hud_hover")

        # 1. Block 1: Actions (configured dynamically according to ai_enabled)
        self.canvas.create_image(x, y, image=self._cap1_photo, anchor=tk.NW, tags=("lens_hud", "lens_hud_bg"))

        for key, label in self.b1_items:
            cx_off, _ = self._get_item_layout(key)
            cx = x + cx_off

            # Draw active mode accent pill behind the selected button only when active
            if self.active_mode and key == self.active_mode:
                self.canvas.create_image(cx, y + self.b1_h // 2, image=self._act_pill_photos.get(key, self._act_pill_photo), tags=("lens_hud", "lens_hud_act"))

            self.canvas.create_image(cx, y + 18, image=self._photo_icons[key], tags=("lens_hud", "lens_hud_fg"))
            self.canvas.create_text(
                cx,
                y + 40,
                text=label,
                fill="#ffffff",
                font=("Segoe UI", 8, "bold"),
                tags=("lens_hud", "lens_hud_fg"),
            )

        # 2. Block 2: Management (Копировать, Закрыть)
        bx2 = x + self.b1_w + self.gap
        self.canvas.create_image(bx2, y, image=self._cap2_photo, anchor=tk.NW, tags=("lens_hud", "lens_hud_bg"))

        # Copy
        cx_off_copy, _ = self._get_item_layout("copy")
        cx_copy = bx2 + cx_off_copy
        if self.active_mode and self.active_mode == "copy":
            self.canvas.create_image(cx_copy, y + self.b2_h // 2, image=self._act_pill_photos.get("copy", self._act_pill_photo), tags=("lens_hud", "lens_hud_act"))

        self.canvas.create_image(cx_copy, y + 18, image=self._photo_icons["copy"], tags=("lens_hud", "lens_hud_fg"))
        self.canvas.create_text(
            cx_copy,
            y + 40,
            text="Копировать",
            fill="#ffffff",
            font=("Segoe UI", 7, "bold"),
            tags=("lens_hud", "lens_hud_fg"),
        )

        # Close — DANGER RED
        cx_off_close, _ = self._get_item_layout("close")
        cx_close = bx2 + cx_off_close
        if self.active_mode and self.active_mode == "close":
            self.canvas.create_image(cx_close, y + self.b2_h // 2, image=self._act_pill_photos.get("close", self._act_pill_photo), tags=("lens_hud", "lens_hud_act"))

        self.canvas.create_image(cx_close, y + 18, image=self._photo_icons["close"], tags=("lens_hud", "lens_hud_fg"))
        self.canvas.create_text(
            cx_close,
            y + 40,
            text="Закрыть",
            fill="#ff5252",
            font=("Segoe UI", 8, "bold"),
            tags=("lens_hud", "lens_hud_fg"),
        )

        self.is_visible = True
        self._update_buttons_list()

    def hide(self) -> None:
        """Hides HUD from canvas."""
        self.canvas.delete("lens_hud")
        self.canvas.delete("lens_hud_hover")
        self.is_visible = False
        self._hovered_btn = None
        self.active_mode = None

    def is_inside(self, mx: int, my: int) -> bool:
        """Checks if (mx, my) is within Block 1 or Block 2 bounds."""
        if not self.is_visible:
            return False
        # Block 1 check
        if self.x <= mx <= self.x + self.b1_w and self.y <= my <= self.y + self.b1_h:
            return True
        # Block 2 check
        bx2 = self.x + self.b1_w + self.gap
        if bx2 <= mx <= bx2 + self.b2_w and self.y <= my <= self.y + self.b2_h:
            return True
        return False

    def hit_test(self, mx: int, my: int) -> Optional[str]:
        """Returns the button key under cursor ('text', 'translate', 'lens', 'ai', 'copy', 'close') or None."""
        if not self.is_visible:
            return None

        # Check Block 1
        if self.x <= mx <= self.x + self.b1_w and self.y <= my <= self.y + self.b1_h:
            rel_x = mx - self.x
            if len(self.b1_items) == 4:
                if rel_x < 71:
                    return "text"
                elif rel_x < 139:
                    return "translate"
                elif rel_x < 201:
                    return "ai"
                elif rel_x <= self.b1_w:
                    return "lens"
            else:
                if rel_x < 71:
                    return "text"
                elif rel_x < 138:
                    return "translate"
                elif rel_x <= self.b1_w:
                    return "lens"
            return "bg"

        # Check Block 2
        bx2 = self.x + self.b1_w + self.gap
        if bx2 <= mx <= bx2 + self.b2_w and self.y <= my <= self.y + self.b2_h:
            rel_x = mx - bx2
            if rel_x < 82:
                return "copy"
            elif rel_x <= self.b2_w:
                return "close"
            return "bg"

        return None

    def handle_motion(self, mx: int, my: int) -> None:
        """Draws hover highlight under current button."""
        target = self.hit_test(mx, my)
        if target == self._hovered_btn:
            return

        self._hovered_btn = target
        self.canvas.delete("lens_hud_hover")

        if not target or target == "bg":
            return

        # Do not draw hover pill over the active button (it already has the active accent pill)
        if self.active_mode and target == self.active_mode:
            return

        # Render hover highlight pill behind the item
        keys_b1 = [k for k, _ in self.b1_items]
        if target in keys_b1:
            cx_off, _ = self._get_item_layout(target)
            cx = self.x + cx_off
            cy = self.y + self.b1_h // 2
            hov_photo = self._hov_pill_photos.get(target, self._hov_norm_photo)
            self.canvas.create_image(cx, cy, image=hov_photo, tags=("lens_hud_hover", "lens_hud"))
            self.canvas.tag_lower("lens_hud_hover", "lens_hud_fg")
        elif target in ("copy", "close"):
            bx2 = self.x + self.b1_w + self.gap
            cx_off, _ = self._get_item_layout(target)
            cx = bx2 + cx_off
            cy = self.y + self.b2_h // 2
            hov_photo = self._hov_pill_photos.get(target, self._hov_norm_photo)
            self.canvas.create_image(cx, cy, image=hov_photo, tags=("lens_hud_hover", "lens_hud"))
            self.canvas.tag_lower("lens_hud_hover", "lens_hud_fg")

    def clear_hover(self) -> None:
        """Removes hover highlight."""
        if self._hovered_btn is not None:
            self._hovered_btn = None
            self.canvas.delete("lens_hud_hover")

    def handle_click(self, mx: int, my: int) -> bool:
        """Executes corresponding action if a button was clicked. Returns True if click was handled."""
        target = self.hit_test(mx, my)
        if not target:
            return False

        if target in ("text", "translate", "ai", "lens"):
            self.set_active_mode(target)

        if target == "text":
            self.on_text()
        elif target == "translate":
            self.on_translate()
        elif target == "ai":
            self.on_ai()
        elif target == "lens":
            self.on_lens()
        elif target == "copy":
            self.on_copy()
        elif target == "close":
            self.on_close()

        return True


class FloatingHUD:
    """Backwards-compatible standalone HUD window fallback."""

    def __init__(
        self,
        image_path: Path,
        ocr_text: str,
        qr_data: Optional[str] = None,
        gemini_key: Optional[str] = None,
        gemini_model: str = "gemini-3.5-flash-lite",
        ai_enabled: bool = True,
        target_lang: str = "ru",
        sound_enabled: bool = True,
    ) -> None:
        self.image_path = image_path
        self.ocr_text = ocr_text
        self.qr_data = qr_data
        self.gemini_key = gemini_key
        self.gemini_model = gemini_model
        self.ai_enabled = ai_enabled
        self.target_lang = target_lang
        self.sound_enabled = sound_enabled
        self.clipboard = ClipboardManager()

    def show(self) -> None:
        """Renders standalone fallback dialog."""
        root = tk.Toplevel() if tk._default_root else tk.Tk()
        root.title("ScreenLens Actions")
        root.attributes("-topmost", True)
        root.configure(bg="#202124")
        root.geometry("540x440")
        root.resizable(True, True)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook", background="#202124", borderwidth=0)
        style.configure("TNotebook.Tab", background="#34373c", foreground="#ffffff", padding=[12, 6])
        style.map("TNotebook.Tab", background=[("selected", "#ffffff")], foreground=[("selected", "#121212")])

        header = tk.Frame(root, bg="#16181b", pady=8, padx=12)
        header.pack(fill=tk.X)

        title_lbl = tk.Label(
            header,
            text="ScreenLens — Google Lens",
            font=("Segoe UI", 11, "bold"),
            fg="#ffffff",
            bg="#16181b",
        )
        title_lbl.pack(side=tk.LEFT)

        def do_copy():
            text_to_copy = self.qr_data if self.qr_data else self.ocr_text
            self.clipboard.copy(text_to_copy)
            copy_btn.set_text("✓ Скопировано!")
            root.after(1500, lambda: copy_btn.set_text("Скопировать"))

        copy_btn = CapsuleButton(
            header,
            text="Скопировать",
            style="primary",
            card_bg="#16181b",
            width=110,
            height=30,
            command=do_copy,
        )
        copy_btn.pack(side=tk.RIGHT)

        lens_btn = CapsuleButton(
            header,
            text="Google Lens",
            style="secondary",
            card_bg="#16181b",
            width=100,
            height=30,
            command=lambda: open_in_google_lens(self.image_path),
        )
        lens_btn.pack(side=tk.RIGHT, padx=6)

        notebook = ttk.Notebook(root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        tab_ocr = tk.Frame(notebook, bg="#202124")
        notebook.add(tab_ocr, text="Текст")

        txt_ocr = tk.Text(
            tab_ocr,
            wrap=tk.WORD,
            bg="#16181b",
            fg="#ffffff",
            insertbackground="#ffffff",
            font=("Segoe UI", 10),
            relief=tk.FLAT,
            padx=8,
            pady=8,
        )
        txt_ocr.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        txt_ocr.insert(tk.END, self.ocr_text if self.ocr_text else "[Текст не найден]")

        tab_tr = tk.Frame(notebook, bg="#202124")
        notebook.add(tab_tr, text="Перевод")

        tr_toolbar = tk.Frame(tab_tr, bg="#202124", pady=4)
        tr_toolbar.pack(fill=tk.X)

        tr_lbl = tk.Label(tr_toolbar, text=f"Язык перевода: {self.target_lang.upper()}", fg="#9e9e9e", bg="#202124")
        tr_lbl.pack(side=tk.LEFT)

        txt_tr = tk.Text(
            tab_tr,
            wrap=tk.WORD,
            bg="#16181b",
            fg="#ffffff",
            font=("Segoe UI", 10),
            relief=tk.FLAT,
            padx=8,
            pady=8,
        )
        txt_tr.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        def execute_translation():
            txt_tr.delete("1.0", tk.END)
            txt_tr.insert(tk.END, "Выполняется перевод...")
            root.update_idletasks()
            res = translate_text(self.ocr_text, target_lang=self.target_lang)
            txt_tr.delete("1.0", tk.END)
            txt_tr.insert(tk.END, res)

        tr_btn = CapsuleButton(
            tr_toolbar,
            text="Перевести заново",
            style="secondary",
            card_bg="#202124",
            width=130,
            height=28,
            command=execute_translation,
        )
        tr_btn.pack(side=tk.RIGHT)
