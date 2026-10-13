"""Interactive Selectable OCR Text Overlay Layer for ScreenLens (Google Lens Variant A).

Provides interactive text selection directly over the captured screenshot region on the Tkinter Canvas.
The original screenshot remains 100% visually untouched:
- No OCR font rendering on top of original letters.
- No inpainting or background color replacement in standard selection mode.
- Invisible interactive selection regions overlay the image.
- Mouse hover and drag provide smooth translucent highlighting (stippled Google Blue tint)
  over original words without obscuring or modifying the underlying image.
- Supports word, phrase, multi-line drag selection, double-click word selection,
  triple-click line selection, floating copy pill, and Ctrl+C clipboard copying.
"""

from __future__ import annotations

import logging
import re
import tkinter as tk
from dataclasses import dataclass
from tkinter import font as tkfont
from typing import Callable, Optional

import numpy as np
from PIL import Image, ImageDraw, ImageTk

from core.models import OCRResult
from ui.floating_hud import render_capsule_image

try:
    import cv2
except ImportError:
    cv2 = None


@dataclass
class TextWord:
    """Represents a single word recognized by OCR on the Canvas."""

    index: int
    line_index: int
    region_index: int
    word_index_in_region: int
    text: str
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def width(self) -> float:
        return max(1.0, self.x2 - self.x1)

    @property
    def height(self) -> float:
        return max(1.0, self.y2 - self.y1)

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2.0

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2.0

    def contains(self, px: float, py: float, pad: float = 2.0) -> bool:
        """Returns True if point (px, py) is inside word bounds with padding."""
        return (self.x1 - pad <= px <= self.x2 + pad) and (self.y1 - pad <= py <= self.y2 + pad)


@dataclass
class TextRegion:
    """Represents a cohesive horizontal interactive text region within a line."""

    region_id: int
    line_index: int
    words: list[TextWord]
    x1: float
    y1: float
    x2: float
    y2: float
    bg_color: tuple[int, int, int] = (24, 26, 30)
    fg_color: str = "#ffffff"
    font_size: int = 10

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def width(self) -> float:
        return max(1.0, self.x2 - self.x1)

    @property
    def height(self) -> float:
        return max(1.0, self.y2 - self.y1)

    @property
    def hex_bg(self) -> str:
        r, g, b = self.bg_color
        return f"#{r:02x}{g:02x}{b:02x}"

    def contains(self, px: float, py: float, pad: float = 2.0) -> bool:
        return (self.x1 - pad <= px <= self.x2 + pad) and (self.y1 - pad <= py <= self.y2 + pad)

    def get_selection_sub_rect(self, selected_indices: set[int]) -> Optional[tuple[float, float, float, float]]:
        """Returns a single continuous bounding rectangle covering all selected words in this region."""
        sel_words = [w for w in self.words if w.index in selected_indices]
        if not sel_words:
            return None
        min_x = min(w.x1 for w in sel_words)
        max_x = max(w.x2 for w in sel_words)
        return (min_x, self.y1, max_x, self.y2)


# Backward-compatibility alias
TextSegment = TextRegion


def inpaint_text_segments(
    pil_img: Image.Image,
    segments: list[tuple[int, int, int, int]],
) -> tuple[Image.Image, list[tuple[int, int, int]]]:
    """Utility function: Masks text regions within image. Preserved for backward compatibility."""
    img_rgb = pil_img.convert("RGB")
    arr = np.array(img_rgb)
    h_img, w_img, _ = arr.shape

    bg_colors: list[tuple[int, int, int]] = []
    complex_mask = np.zeros((h_img, w_img), dtype=np.uint8)
    has_complex = False

    for (x1, y1, x2, y2) in segments:
        x1 = max(0, min(w_img - 1, int(x1)))
        y1 = max(0, min(h_img - 1, int(y1)))
        x2 = max(0, min(w_img, int(x2)))
        y2 = max(0, min(h_img, int(y2)))
        if x2 <= x1 or y2 <= y1:
            bg_colors.append((24, 26, 30))
            continue

        pad = 3
        top_s = arr[max(0, y1 - pad):y1, max(0, x1 - pad):min(w_img, x2 + pad)]
        bot_s = arr[y2:min(h_img, y2 + pad), max(0, x1 - pad):min(w_img, x2 + pad)]
        left_s = arr[y1:y2, max(0, x1 - pad):x1]
        right_s = arr[y1:y2, x2:min(w_img, x2 + pad)]

        border_strips = [s.reshape(-1, 3) for s in (top_s, bot_s, left_s, right_s) if s.size > 0]
        if border_strips:
            all_bp = np.vstack(border_strips)
            std_val = float(np.mean(np.std(all_bp, axis=0)))
            med_col = np.median(all_bp, axis=0).astype(int)
            col_tuple = (int(med_col[0]), int(med_col[1]), int(med_col[2]))
        else:
            std_val = 0.0
            col_tuple = (24, 26, 30)

        bg_colors.append(col_tuple)

        if std_val > 28.0 and cv2 is not None:
            complex_mask[y1:y2, x1:x2] = 255
            has_complex = True
        else:
            arr[y1:y2, x1:x2] = col_tuple

    if has_complex and cv2 is not None:
        try:
            arr = cv2.inpaint(arr, complex_mask, inpaintRadius=3, flags=cv2.INPAINT_TELEA)
        except Exception:
            pass

    return Image.fromarray(arr), bg_colors


class SelectableTextLayer:
    """Interactive text selection layer on Canvas over the original untouched screenshot."""

    TAG_LAYER = "ocr_text_layer"
    TAG_REGION = "ocr_region_elem"
    TAG_UNDERLAY = "ocr_underlay_elem"
    TAG_WORD = "ocr_word_elem"
    TAG_SELECTION = "ocr_selection_elem"
    TAG_HOVER = "ocr_hover_elem"
    TAG_FLOAT_BTN = "ocr_float_copy_btn"

    def __init__(
        self,
        canvas: tk.Canvas,
        root: tk.Tk,
        on_copy_callback: Optional[Callable[[str], None]] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.canvas = canvas
        self.root = root
        self.on_copy_callback = on_copy_callback
        self._logger = logger or logging.getLogger(__name__)

        self.words: list[TextWord] = []
        self.lines: list[list[TextWord]] = []
        self.regions: list[TextRegion] = []
        self.selected_indices: set[int] = set()
        self.box: tuple[int, int, int, int] = (0, 0, 0, 0)
        self.is_active: bool = False
        self.mode: str = "text"
        self.translated_text: str = ""

        self.crop_img: Optional[Image.Image] = None
        self.cutout_item_id: Optional[int] = None
        self.original_crop_photo: Optional[tk.PhotoImage] = None
        self._active_crop_photo: Optional[ImageTk.PhotoImage] = None

        self._drag_start_word_idx: Optional[int] = None
        self._drag_start_xy: Optional[tuple[int, int]] = None
        self._is_dragging: bool = False
        self._hovered_region_id: Optional[int] = None

        # Floating copy button bounds: (x1, y1, x2, y2)
        self._btn_rect: Optional[tuple[int, int, int, int]] = None
        self._btn_photo: Optional[ImageTk.PhotoImage] = None

    @property
    def segments(self) -> list[TextRegion]:
        """Backward compatibility property returning regions."""
        return self.regions

    @segments.setter
    def segments(self, value: list[TextRegion]) -> None:
        self.regions = value

    def load_from_ocr(
        self,
        ocr_result: OCRResult,
        box: tuple[int, int, int, int],
        crop_img: Optional[Image.Image] = None,
        cutout_item_id: Optional[int] = None,
        original_crop_photo: Optional[tk.PhotoImage] = None,
    ) -> int:
        """Builds words and regions from OCRResult relative to box coordinates.
        Original image remains untouched.
        """
        self.clear()
        self.mode = "text"
        self.translated_text = ""
        self.box = box
        self.crop_img = crop_img.copy() if crop_img else None
        self.cutout_item_id = cutout_item_id
        self.original_crop_photo = original_crop_photo

        bx1, by1, bx2, by2 = box
        raw_lines = ocr_result.lines if ocr_result.lines else ()

        # 1. Parse raw lines
        parsed_lines_raw: list[list[dict]] = []
        if raw_lines:
            for l_dict in raw_lines:
                w_list = l_dict.get("words", [])
                valid = []
                for w_data in w_list:
                    txt = w_data.get("text", "").strip()
                    if not txt:
                        continue
                    bx = w_data.get("bounding_rect", {})
                    wx = float(bx.get("x", 0.0)) + bx1
                    wy = float(bx.get("y", 0.0)) + by1
                    ww = max(4.0, float(bx.get("width", 10.0)))
                    wh = max(8.0, float(bx.get("height", 12.0)))
                    valid.append({
                        "text": txt,
                        "x1": wx,
                        "y1": wy,
                        "x2": wx + ww,
                        "y2": wy + wh,
                        "h": wh,
                    })
                if valid:
                    valid.sort(key=lambda item: item["x1"])
                    parsed_lines_raw.append(valid)

        # Fallback if engine gave only text without word bounding boxes
        if not parsed_lines_raw and ocr_result.cleaned_text:
            text_lines = [l.strip() for l in ocr_result.cleaned_text.splitlines() if l.strip()]
            if text_lines:
                avail_h = max(24.0, float(by2 - by1))
                avail_w = max(40.0, float(bx2 - bx1))
                line_h = avail_h / max(len(text_lines), 1)
                for l_idx, l_str in enumerate(text_lines):
                    tokens = l_str.split()
                    if not tokens:
                        continue
                    tok_w = (avail_w - 20) / max(len(tokens), 1)
                    ly1 = by1 + l_idx * line_h + 3
                    ly2 = ly1 + line_h - 4
                    valid = []
                    for w_idx, tok in enumerate(tokens):
                        lx1 = bx1 + 10 + w_idx * tok_w
                        lx2 = lx1 + tok_w - 4
                        valid.append({
                            "text": tok,
                            "x1": lx1,
                            "y1": ly1,
                            "x2": lx2,
                            "y2": ly2,
                            "h": ly2 - ly1,
                        })
                    if valid:
                        parsed_lines_raw.append(valid)

        if not parsed_lines_raw:
            self.words = []
            self.lines = []
            self.regions = []
            return 0

        # Sort lines top-to-bottom
        parsed_lines_raw.sort(key=lambda line: min(w["y1"] for w in line))

        # 2. Compute stable uniform height per line with increased selection height
        line_vertical_bounds: list[tuple[float, float]] = []
        for line_w in parsed_lines_raw:
            heights = [w["h"] for w in line_w]
            median_h = float(np.median(heights))
            y1_vals = [w["y1"] for w in line_w]
            median_y1 = float(np.median(y1_vals))
            line_y1 = median_y1
            line_y2 = median_y1 + median_h
            line_vertical_bounds.append((line_y1, line_y2))

        # 3. Ensure neighboring lines strictly do NOT overlap vertically
        for k in range(len(line_vertical_bounds) - 1):
            curr_y1, curr_y2 = line_vertical_bounds[k]
            next_y1, next_y2 = line_vertical_bounds[k + 1]
            if curr_y2 >= next_y1:
                mid = (curr_y2 + next_y1) / 2.0
                line_vertical_bounds[k] = (curr_y1, mid - 0.5)
                line_vertical_bounds[k + 1] = (mid + 0.5, next_y2)

        # 4. Form cohesive regions within lines with gap-based column splitting
        all_words: list[TextWord] = []
        all_regions: list[TextRegion] = []
        all_lines_words: list[list[TextWord]] = []
        global_word_idx = 0
        region_id = 0

        for l_idx, (line_w, (stable_y1, stable_y2)) in enumerate(zip(parsed_lines_raw, line_vertical_bounds)):
            line_h = max(8.0, stable_y2 - stable_y1)
            line_words_accum: list[TextWord] = []

            # Determine adaptive break threshold for this line
            positive_gaps = [
                line_w[i + 1]["x1"] - line_w[i]["x2"]
                for i in range(len(line_w) - 1)
                if line_w[i + 1]["x1"] - line_w[i]["x2"] > 0
            ]
            typical_gap = float(np.median(positive_gaps)) if positive_gaps else (line_h * 0.35)
            break_threshold = max(line_h * 1.6, typical_gap * 2.8, 28.0)

            current_reg_raw: list[dict] = [line_w[0]]

            for w in line_w[1:]:
                prev_w = current_reg_raw[-1]
                gap = w["x1"] - prev_w["x2"]
                if gap > break_threshold:
                    # Split into separate region
                    reg_x1 = current_reg_raw[0]["x1"] - 2.0
                    reg_x2 = current_reg_raw[-1]["x2"] + 2.0
                    reg_words: list[TextWord] = []
                    for w_idx_in_reg, rw in enumerate(current_reg_raw):
                        tw = TextWord(
                            index=global_word_idx,
                            line_index=l_idx,
                            region_index=region_id,
                            word_index_in_region=w_idx_in_reg,
                            text=rw["text"],
                            x1=rw["x1"],
                            y1=stable_y1,
                            x2=rw["x2"],
                            y2=stable_y2,
                        )
                        global_word_idx += 1
                        reg_words.append(tw)
                        all_words.append(tw)
                        line_words_accum.append(tw)

                    region = TextRegion(
                        region_id=region_id,
                        line_index=l_idx,
                        words=reg_words,
                        x1=reg_x1,
                        y1=stable_y1,
                        x2=reg_x2,
                        y2=stable_y2,
                    )
                    all_regions.append(region)
                    region_id += 1
                    current_reg_raw = [w]
                else:
                    current_reg_raw.append(w)

            if current_reg_raw:
                reg_x1 = current_reg_raw[0]["x1"] - 2.0
                reg_x2 = current_reg_raw[-1]["x2"] + 2.0
                reg_words = []
                for w_idx_in_reg, rw in enumerate(current_reg_raw):
                    tw = TextWord(
                        index=global_word_idx,
                        line_index=l_idx,
                        region_index=region_id,
                        word_index_in_region=w_idx_in_reg,
                        text=rw["text"],
                        x1=rw["x1"],
                        y1=stable_y1,
                        x2=rw["x2"],
                        y2=stable_y2,
                    )
                    global_word_idx += 1
                    reg_words.append(tw)
                    all_words.append(tw)
                    line_words_accum.append(tw)

                region = TextRegion(
                    region_id=region_id,
                    line_index=l_idx,
                    words=reg_words,
                    x1=reg_x1,
                    y1=stable_y1,
                    x2=reg_x2,
                    y2=stable_y2,
                )
                all_regions.append(region)
                region_id += 1

            if line_words_accum:
                all_lines_words.append(line_words_accum)

        self.words = all_words
        self.lines = all_lines_words
        self.regions = all_regions
        return len(self.words)

    def _estimate_local_bg(self, rx1: float, ry1: float, rx2: float, ry2: float) -> tuple[int, int, int]:
        """Samples border pixels from crop_img around rectangle (rx1, ry1, rx2, ry2) in canvas coordinates."""
        if self.crop_img is None:
            return (24, 26, 30)
        try:
            bx1, by1, _, _ = self.box
            cx1 = int(round(rx1 - bx1))
            cy1 = int(round(ry1 - by1))
            cx2 = int(round(rx2 - bx1))
            cy2 = int(round(ry2 - by1))

            arr = np.array(self.crop_img.convert("RGB"))
            h_img, w_img, _ = arr.shape

            x1 = max(0, min(w_img - 1, cx1))
            y1 = max(0, min(h_img - 1, cy1))
            x2 = max(0, min(w_img, cx2))
            y2 = max(0, min(h_img, cy2))

            if x2 <= x1 or y2 <= y1:
                return (24, 26, 30)

            pad = 4
            top_s = arr[max(0, y1 - pad):y1, max(0, x1 - pad):min(w_img, x2 + pad)]
            bot_s = arr[y2:min(h_img, y2 + pad), max(0, x1 - pad):min(w_img, x2 + pad)]
            left_s = arr[y1:y2, max(0, x1 - pad):x1]
            right_s = arr[y1:y2, x2:min(w_img, x2 + pad)]

            border_strips = [s.reshape(-1, 3) for s in (top_s, bot_s, left_s, right_s) if s.size > 0]
            if border_strips:
                all_bp = np.vstack(border_strips)
                med_col = np.median(all_bp, axis=0).astype(int)
                return (int(med_col[0]), int(med_col[1]), int(med_col[2]))
        except Exception as exc:
            self._logger.debug("Local bg estimation failed: %s", exc)

        return (24, 26, 30)

    def load_translation(
        self,
        ocr_result: OCRResult,
        translated_text: str,
        box: tuple[int, int, int, int],
        crop_img: Optional[Image.Image] = None,
        cutout_item_id: Optional[int] = None,
        original_crop_photo: Optional[tk.PhotoImage] = None,
    ) -> int:
        """Builds words and regions from translated text mapped over original OCR layout.
        Uses underlay plates to mask original text and crisp translated typography.
        """
        self.clear()
        self.mode = "translate"
        self.translated_text = translated_text
        self.box = box
        self.crop_img = crop_img.copy() if crop_img else None
        self.cutout_item_id = cutout_item_id
        self.original_crop_photo = original_crop_photo

        clean_tr = translated_text.strip()
        if not clean_tr:
            return 0

        bx1, by1, bx2, by2 = box
        raw_lines = ocr_result.lines if ocr_result.lines else ()

        # 1. Parse raw OCR lines to extract spatial bounding layout
        parsed_lines_raw: list[list[dict]] = []
        if raw_lines:
            for l_dict in raw_lines:
                w_list = l_dict.get("words", [])
                valid = []
                for w_data in w_list:
                    txt = w_data.get("text", "").strip()
                    if not txt:
                        continue
                    bx = w_data.get("bounding_rect", {})
                    wx = float(bx.get("x", 0.0)) + bx1
                    wy = float(bx.get("y", 0.0)) + by1
                    ww = max(4.0, float(bx.get("width", 10.0)))
                    wh = max(8.0, float(bx.get("height", 12.0)))
                    valid.append({
                        "text": txt,
                        "x1": wx,
                        "y1": wy,
                        "x2": wx + ww,
                        "y2": wy + wh,
                        "h": wh,
                    })
                if valid:
                    valid.sort(key=lambda item: item["x1"])
                    parsed_lines_raw.append(valid)

        # Fallback if engine gave only text without word bounding boxes
        if not parsed_lines_raw and ocr_result.cleaned_text:
            text_lines = [l.strip() for l in ocr_result.cleaned_text.splitlines() if l.strip()]
            if text_lines:
                avail_h = max(24.0, float(by2 - by1))
                avail_w = max(40.0, float(bx2 - bx1))
                line_h = avail_h / max(len(text_lines), 1)
                for l_idx, l_str in enumerate(text_lines):
                    tokens = l_str.split()
                    if not tokens:
                        continue
                    tok_w = (avail_w - 20) / max(len(tokens), 1)
                    ly1 = by1 + l_idx * line_h + 3
                    ly2 = ly1 + line_h - 4
                    valid = []
                    for w_idx, tok in enumerate(tokens):
                        lx1 = bx1 + 10 + w_idx * tok_w
                        lx2 = lx1 + tok_w - 4
                        valid.append({
                            "text": tok,
                            "x1": lx1,
                            "y1": ly1,
                            "x2": lx2,
                            "y2": ly2,
                            "h": ly2 - ly1,
                        })
                    if valid:
                        parsed_lines_raw.append(valid)

        # Sort OCR lines top-to-bottom
        if parsed_lines_raw:
            parsed_lines_raw.sort(key=lambda line: min(w["y1"] for w in line))

        # Split translated text into lines
        tr_lines = [l.strip() for l in clean_tr.splitlines() if l.strip()]
        if not tr_lines:
            return 0

        # If no OCR lines detected at all, synthesize layout from box
        if not parsed_lines_raw:
            avail_h = max(24.0, float(by2 - by1))
            line_h = avail_h / max(len(tr_lines), 1)
            for l_idx in range(len(tr_lines)):
                ly1 = by1 + l_idx * line_h + 3
                ly2 = ly1 + line_h - 4
                parsed_lines_raw.append([{
                    "text": "",
                    "x1": float(bx1 + 10),
                    "y1": ly1,
                    "x2": float(bx2 - 10),
                    "y2": ly2,
                    "h": ly2 - ly1,
                }])

        # Align translated lines to OCR lines
        n_ocr = len(parsed_lines_raw)
        n_tr = len(tr_lines)
        if n_tr == n_ocr:
            mapped_tr_lines = tr_lines
        elif n_tr < n_ocr:
            mapped_tr_lines = tr_lines + [""] * (n_ocr - n_tr)
        else:
            mapped_tr_lines = tr_lines[:n_ocr - 1] + [" ".join(tr_lines[n_ocr - 1:])]

        # Compute vertical bounds
        line_vertical_bounds: list[tuple[float, float]] = []
        for line_w in parsed_lines_raw:
            heights = [w["h"] for w in line_w]
            median_h = float(np.median(heights))
            y1_vals = [w["y1"] for w in line_w]
            median_y1 = float(np.median(y1_vals))
            line_y1 = median_y1
            line_y2 = median_y1 + median_h
            line_vertical_bounds.append((line_y1, line_y2))

        # Ensure neighboring lines strictly do NOT overlap vertically
        for k in range(len(line_vertical_bounds) - 1):
            curr_y1, curr_y2 = line_vertical_bounds[k]
            next_y1, next_y2 = line_vertical_bounds[k + 1]
            if curr_y2 >= next_y1:
                mid = (curr_y2 + next_y1) / 2.0
                line_vertical_bounds[k] = (curr_y1, mid - 0.5)
                line_vertical_bounds[k + 1] = (mid + 0.5, next_y2)

        # Layout translated words and regions
        all_words: list[TextWord] = []
        all_regions: list[TextRegion] = []
        all_lines_words: list[list[TextWord]] = []
        global_word_idx = 0
        region_id = 0

        for l_idx, (line_w, (stable_y1, stable_y2), tr_line) in enumerate(
            zip(parsed_lines_raw, line_vertical_bounds, mapped_tr_lines)
        ):
            tokens = tr_line.split()
            if not tokens:
                continue

            line_h = max(8.0, stable_y2 - stable_y1)
            orig_x1 = min(w["x1"] for w in line_w)
            orig_x2 = max(w["x2"] for w in line_w)

            font_sz = max(9, min(22, int(line_h * 0.62)))
            try:
                fnt = tkfont.Font(root=self.root, family="Segoe UI", size=font_sz)
                word_widths = [max(4.0, float(fnt.measure(t))) for t in tokens]
                space_w = max(3.0, float(fnt.measure(" ")))
            except Exception:
                word_widths = [max(4.0, float(len(t) * font_sz * 0.6)) for t in tokens]
                space_w = max(3.0, float(font_sz * 0.4))

            total_w = sum(word_widths) + max(0, len(tokens) - 1) * space_w

            # Adaptive font size shrink if text exceeds available box width
            avail_w = max(40.0, float(bx2 - orig_x1 - 8.0))
            if total_w > avail_w and font_sz > 8:
                scale = avail_w / total_w
                new_sz = max(8, int(font_sz * scale))
                if new_sz < font_sz:
                    font_sz = new_sz
                    try:
                        fnt = tkfont.Font(root=self.root, family="Segoe UI", size=font_sz)
                        word_widths = [max(4.0, float(fnt.measure(t))) for t in tokens]
                        space_w = max(3.0, float(fnt.measure(" ")))
                    except Exception:
                        word_widths = [max(4.0, float(len(t) * font_sz * 0.6)) for t in tokens]
                        space_w = max(3.0, float(font_sz * 0.4))
                    total_w = sum(word_widths) + max(0, len(tokens) - 1) * space_w

            reg_x1 = max(float(bx1 + 2), orig_x1 - 2.0)
            reg_x2 = min(float(bx2 - 2), max(orig_x2 + 2.0, reg_x1 + total_w + 4.0))

            # Sample local background color and determine high-contrast text color
            bg_col = self._estimate_local_bg(reg_x1, stable_y1, reg_x2, stable_y2)
            lum = 0.2126 * bg_col[0] + 0.7152 * bg_col[1] + 0.0722 * bg_col[2]
            fg_col = "#1f1f1f" if lum > 140 else "#ffffff"

            cur_x = reg_x1 + 2.0
            reg_words: list[TextWord] = []
            line_words_accum: list[TextWord] = []

            for tok_idx, tok in enumerate(tokens):
                ww = word_widths[tok_idx]
                tw = TextWord(
                    index=global_word_idx,
                    line_index=l_idx,
                    region_index=region_id,
                    word_index_in_region=tok_idx,
                    text=tok,
                    x1=cur_x,
                    y1=stable_y1,
                    x2=cur_x + ww,
                    y2=stable_y2,
                )
                global_word_idx += 1
                reg_words.append(tw)
                all_words.append(tw)
                line_words_accum.append(tw)
                cur_x += ww + space_w

            region = TextRegion(
                region_id=region_id,
                line_index=l_idx,
                words=reg_words,
                x1=reg_x1,
                y1=stable_y1,
                x2=max(reg_x2, cur_x),
                y2=stable_y2,
                bg_color=bg_col,
                fg_color=fg_col,
                font_size=font_sz,
            )
            all_regions.append(region)
            region_id += 1

            if line_words_accum:
                all_lines_words.append(line_words_accum)

        self.words = all_words
        self.lines = all_lines_words
        self.regions = all_regions
        return len(self.words)

    def show(self) -> None:
        """Activates text or translate selection mode.
        In text mode, original screenshot remains 100% untouched with invisible hitboxes.
        In translate mode, underlay plates mask original text and crisp translated typography is rendered.
        """
        self.canvas.delete(self.TAG_LAYER)
        self.is_active = True
        self.selected_indices.clear()
        try:
            self.canvas.focus_set()
        except Exception:
            pass

        if self.mode == "translate":
            # 1. Underlay plates to mask original text
            for reg in self.regions:
                self.canvas.create_rectangle(
                    reg.x1 - 2,
                    reg.y1 - 1,
                    reg.x2 + 2,
                    reg.y2 + 1,
                    fill=reg.hex_bg,
                    outline=reg.hex_bg,
                    tags=(self.TAG_LAYER, self.TAG_UNDERLAY, f"underlay_{reg.region_id}"),
                )

            # 2. Crisp translated words
            for w in self.words:
                reg = self.regions[w.region_index] if w.region_index < len(self.regions) else None
                fg = getattr(reg, "fg_color", "#ffffff") if reg else "#ffffff"
                f_sz = getattr(reg, "font_size", 10) if reg else 10
                self.canvas.create_text(
                    w.x1,
                    (w.y1 + w.y2) / 2.0,
                    text=w.text,
                    fill=fg,
                    font=("Segoe UI", f_sz),
                    anchor=tk.W,
                    tags=(self.TAG_LAYER, self.TAG_WORD, f"word_{w.index}"),
                )

        # Create invisible interactive area hitboxes over text regions
        for reg in self.regions:
            self.canvas.create_rectangle(
                reg.x1,
                reg.y1,
                reg.x2,
                reg.y2,
                fill="",
                outline="",
                tags=(self.TAG_LAYER, self.TAG_REGION, f"reg_{reg.region_id}"),
            )

    def hide(self) -> None:
        """Deactivates text selection mode and removes all selection highlights."""
        self.canvas.delete(self.TAG_LAYER)
        self.selected_indices.clear()
        self.is_active = False
        self._hovered_region_id = None
        self._drag_start_word_idx = None
        self._drag_start_xy = None
        self._is_dragging = False
        self._btn_rect = None
        if self.cutout_item_id is not None and self.original_crop_photo is not None:
            try:
                self.canvas.itemconfig(self.cutout_item_id, image=self.original_crop_photo)
            except Exception:
                pass
        self._active_crop_photo = None

    def clear(self) -> None:
        """Full cleanup."""
        self.hide()
        self.words.clear()
        self.lines.clear()
        self.regions.clear()
        self.crop_img = None
        self.cutout_item_id = None
        self.original_crop_photo = None
        self._active_crop_photo = None

    # Hit Testing
    def hit_test_word(self, mx: int, my: int) -> Optional[TextWord]:
        """Returns the word under (mx, my) or None."""
        for w in self.words:
            if w.contains(mx, my, pad=2.0):
                return w
        return None

    def hit_test_region(self, mx: int, my: int) -> Optional[TextRegion]:
        """Returns the text region under (mx, my) or None."""
        for reg in self.regions:
            if reg.contains(mx, my, pad=2.0):
                return reg
        return None

    def find_closest_word(self, mx: int, my: int) -> Optional[TextWord]:
        """Finds closest word near (mx, my) in reading proximity."""
        # 1. Prefer word on same horizontal band
        best_w: Optional[TextWord] = None
        min_dist = float("inf")
        for w in self.words:
            if w.y1 - 4 <= my <= w.y2 + 4:
                if w.x1 <= mx <= w.x2:
                    return w
                d = min(abs(mx - w.x1), abs(mx - w.x2))
                if d < min_dist:
                    min_dist = d
                    best_w = w
        if best_w and min_dist < 60:
            return best_w

        # 2. Fallback to center Euclidean distance
        for w in self.words:
            dist = (w.cx - mx) ** 2 + (w.cy - my) ** 2
            if dist < min_dist:
                min_dist = dist
                best_w = w
        return best_w

    def is_inside_copy_button(self, mx: int, my: int) -> bool:
        """Returns True if (mx, my) is inside floating copy pill."""
        if self._btn_rect is None:
            return False
        bx1, by1, bx2, by2 = self._btn_rect
        return bx1 <= mx <= bx2 and by1 <= my <= by2

    # Selection State Management
    def select_range(self, start_idx: int, end_idx: int) -> None:
        """Selects all words in reading order between start_idx and end_idx."""
        min_i = min(start_idx, end_idx)
        max_i = max(start_idx, end_idx)
        self.selected_indices = set(range(min_i, max_i + 1))
        self.redraw_selection()

    def select_all(self) -> None:
        """Selects all words in the layer."""
        self.selected_indices = set(range(len(self.words)))
        self.redraw_selection()

    def clear_selection(self) -> None:
        """Clears all word selections."""
        self.selected_indices.clear()
        self.redraw_selection()

    def get_selected_text(self) -> str:
        """Returns selected text in reading order with line breaks and normal spaces."""
        if not self.selected_indices:
            return ""
        sel_words = [w for w in self.words if w.index in self.selected_indices]
        sel_words.sort(key=lambda w: w.index)

        lines_map: dict[int, list[TextWord]] = {}
        for w in sel_words:
            lines_map.setdefault(w.line_index, []).append(w)

        res_lines: list[str] = []
        for l_idx in sorted(lines_map.keys()):
            l_words = lines_map[l_idx]
            l_words.sort(key=lambda w: w.x1)
            res_lines.append(" ".join(w.text for w in l_words))

        return "\n".join(res_lines)

    def get_all_text(self) -> str:
        """Returns all recognized text in reading order with line breaks and normal spaces."""
        if not self.words:
            return ""
        lines_map: dict[int, list[TextWord]] = {}
        for w in self.words:
            lines_map.setdefault(w.line_index, []).append(w)

        res_lines: list[str] = []
        for l_idx in sorted(lines_map.keys()):
            l_words = lines_map[l_idx]
            l_words.sort(key=lambda w: w.x1)
            res_lines.append(" ".join(w.text for w in l_words))

        return "\n".join(res_lines)

    # Redraw Selection Highlights & Floating Pill
    def redraw_selection(self) -> None:
        """Renders cohesive translucent selection highlight and floating copy pill over text."""
        self.canvas.delete(self.TAG_SELECTION)
        self.canvas.delete(self.TAG_FLOAT_BTN)
        self._btn_rect = None

        if not self.selected_indices:
            if self.mode == "translate":
                for w in self.words:
                    reg = self.regions[w.region_index] if w.region_index < len(self.regions) else None
                    base_fg = getattr(reg, "fg_color", "#ffffff") if reg else "#ffffff"
                    self.canvas.itemconfig(f"word_{w.index}", fill=base_fg)
            else:
                if self.cutout_item_id is not None and self.original_crop_photo is not None:
                    try:
                        self.canvas.itemconfig(self.cutout_item_id, image=self.original_crop_photo)
                    except Exception:
                        pass
            return

        sel_words = [w for w in self.words if w.index in self.selected_indices]
        if not sel_words:
            if self.mode == "text" and self.cutout_item_id is not None and self.original_crop_photo is not None:
                try:
                    self.canvas.itemconfig(self.cutout_item_id, image=self.original_crop_photo)
                except Exception:
                    pass
            return

        # 1. Collect cohesive continuous sub-rectangles per region with increased selection height
        raw_highlight_boxes: list[tuple[float, float, float, float]] = []
        for reg in self.regions:
            sub_rect = reg.get_selection_sub_rect(self.selected_indices)
            if sub_rect is not None:
                rx1, ry1, rx2, ry2 = sub_rect
                line_h = max(8.0, ry2 - ry1)
                v_pad = max(2.5, min(6.0, line_h * 0.20))
                hy1 = ry1 - v_pad
                hy2 = ry2 + v_pad
                raw_highlight_boxes.append((rx1, hy1, rx2, hy2))

        if not raw_highlight_boxes:
            return

        # Sort top-to-bottom and resolve any overlap between consecutive lines
        raw_highlight_boxes.sort(key=lambda b: b[1])
        highlight_boxes: list[tuple[float, float, float, float]] = [raw_highlight_boxes[0]]
        for curr in raw_highlight_boxes[1:]:
            prev_x1, prev_y1, prev_x2, prev_y2 = highlight_boxes[-1]
            c_x1, c_y1, c_x2, c_y2 = curr
            if prev_y2 >= c_y1:
                mid = (prev_y2 + c_y1) / 2.0
                highlight_boxes[-1] = (prev_x1, prev_y1, prev_x2, mid - 0.5)
                c_y1 = mid + 0.5
            highlight_boxes.append((c_x1, c_y1, c_x2, c_y2))

        if self.mode == "translate":
            # In translate mode, highlight with Google Blue rectangles and crisp white text
            for rx1, ry1, rx2, ry2 in highlight_boxes:
                self.canvas.create_rectangle(
                    rx1 - 1,
                    ry1,
                    rx2 + 1,
                    ry2,
                    fill="#1a73e8",
                    outline="#4285f4",
                    width=1,
                    tags=(self.TAG_LAYER, self.TAG_SELECTION),
                )
            for w in self.words:
                reg = self.regions[w.region_index] if w.region_index < len(self.regions) else None
                base_fg = getattr(reg, "fg_color", "#ffffff") if reg else "#ffffff"
                target_fg = "#ffffff" if w.index in self.selected_indices else base_fg
                self.canvas.itemconfig(f"word_{w.index}", fill=target_fg)

            self.canvas.tag_raise(self.TAG_SELECTION)
            self.canvas.tag_raise(self.TAG_WORD)
        else:
            # 2. Render smooth alpha-transparent highlight
            bx1, by1, bx2, by2 = self.box
            has_blended_bg = False

            if self.crop_img is not None and self.cutout_item_id is not None:
                try:
                    base_rgba = self.crop_img.convert("RGBA")
                    overlay = Image.new("RGBA", base_rgba.size, (0, 0, 0, 0))
                    draw = ImageDraw.Draw(overlay)
                    cw, ch = base_rgba.size

                    for rx1, ry1, rx2, ry2 in highlight_boxes:
                        cx1 = max(0, min(cw, int(round(rx1 - bx1 - 1))))
                        cy1 = max(0, min(ch, int(round(ry1 - by1))))
                        cx2 = max(0, min(cw, int(round(rx2 - bx1 + 1))))
                        cy2 = max(0, min(ch, int(round(ry2 - by1))))
                        if cx2 > cx1 and cy2 > cy1:
                            # Soft Google Blue translucent fill with subtle rounded corners
                            draw.rounded_rectangle(
                                [cx1, cy1, cx2, cy2],
                                radius=3,
                                fill=(66, 133, 244, 95),
                                outline=(66, 133, 244, 160),
                                width=1,
                            )

                    blended = Image.alpha_composite(base_rgba, overlay)
                    self._active_crop_photo = ImageTk.PhotoImage(blended)
                    self.canvas.itemconfig(self.cutout_item_id, image=self._active_crop_photo)
                    has_blended_bg = True
                except Exception as e:
                    self._logger.debug("Alpha composite failed, using canvas fallback: %s", e)
                    has_blended_bg = False

            # Canvas selection item (for hit-testing, tests, and outline definition)
            for rx1, ry1, rx2, ry2 in highlight_boxes:
                if has_blended_bg:
                    self.canvas.create_rectangle(
                        rx1 - 1,
                        ry1,
                        rx2 + 1,
                        ry2,
                        fill="",
                        outline="#4285f4",
                        width=1,
                        tags=(self.TAG_LAYER, self.TAG_SELECTION),
                    )
                else:
                    self.canvas.create_rectangle(
                        rx1 - 1,
                        ry1,
                        rx2 + 1,
                        ry2,
                        fill="#1a73e8",
                        stipple="gray25",
                        outline="#4285f4",
                        width=1,
                        tags=(self.TAG_LAYER, self.TAG_SELECTION),
                    )

        # 2. Sleek Floating Copy Stadium Pill positioned relative to selection
        min_x = min(b[0] for b in highlight_boxes)
        max_x = max(b[2] for b in highlight_boxes)
        min_y = min(b[1] for b in highlight_boxes)
        max_y = max(b[3] for b in highlight_boxes)

        btn_w = 124
        btn_h = 28
        btn_cx = int((min_x + max_x) / 2)
        btn_x1 = btn_cx - btn_w // 2
        btn_x2 = btn_x1 + btn_w

        # Position above selection; if too close to top edge, place below
        if min_y >= 38:
            btn_y1 = int(min_y - btn_h - 8)
        else:
            btn_y1 = int(max_y + 8)
        btn_y2 = btn_y1 + btn_h

        self._btn_rect = (btn_x1, btn_y1, btn_x2, btn_y2)

        pill_img = render_capsule_image(
            btn_w,
            btn_h,
            fill=(34, 36, 40, 245),
            outline=(66, 133, 244, 255),
            outline_width=1.2,
        )
        self._btn_photo = ImageTk.PhotoImage(pill_img)

        self.canvas.create_image(
            btn_x1,
            btn_y1,
            image=self._btn_photo,
            anchor=tk.NW,
            tags=(self.TAG_LAYER, self.TAG_FLOAT_BTN),
        )
        self.canvas.create_text(
            btn_cx,
            btn_y1 + btn_h // 2,
            text="📋 Скопировать",
            fill="#ffffff",
            font=("Segoe UI", 9, "bold"),
            tags=(self.TAG_LAYER, self.TAG_FLOAT_BTN),
        )
        self.canvas.tag_raise(self.TAG_FLOAT_BTN)

    # Mouse Event Handling
    def handle_press(self, mx: int, my: int) -> bool:
        """Handles mouse press when in text selection mode."""
        # 1. Floating copy pill click
        if self.is_inside_copy_button(mx, my):
            self.execute_copy()
            return True

        # 2. Direct word click
        target_w = self.hit_test_word(mx, my)
        if target_w is not None:
            self._drag_start_word_idx = target_w.index
            self._drag_start_xy = (mx, my)
            self._is_dragging = True
            self.select_range(target_w.index, target_w.index)
            return True

        # 3. Click inside region (e.g. between words)
        target_reg = self.hit_test_region(mx, my)
        if target_reg is not None and target_reg.words:
            # Find closest word in that region
            closest_w = min(target_reg.words, key=lambda w: abs(w.cx - mx))
            self._drag_start_word_idx = closest_w.index
            self._drag_start_xy = (mx, my)
            self._is_dragging = True
            self.select_range(closest_w.index, closest_w.index)
            return True

        # 4. Click in empty space
        self._drag_start_word_idx = None
        self._drag_start_xy = (mx, my)
        self._is_dragging = True
        self.clear_selection()
        return True

    def handle_drag(self, mx: int, my: int) -> bool:
        """Handles mouse drag when in text selection mode."""
        if not self._is_dragging or self._drag_start_xy is None:
            return False

        # Continuous word-range drag selection
        if self._drag_start_word_idx is not None:
            target_w = self.hit_test_word(mx, my) or self.find_closest_word(mx, my)
            if target_w is not None:
                self.select_range(self._drag_start_word_idx, target_w.index)
            return True

        # Marquee box drag selection in empty space
        sx, sy = self._drag_start_xy
        rx1 = min(sx, mx)
        ry1 = min(sy, my)
        rx2 = max(sx, mx)
        ry2 = max(sy, my)

        self.canvas.delete("ocr_marquee")
        self.canvas.create_rectangle(
            rx1, ry1, rx2, ry2,
            fill="",
            outline="#4285f4",
            width=1,
            dash=(3, 2),
            tags=(self.TAG_LAYER, "ocr_marquee"),
        )

        new_sel = set()
        for w in self.words:
            if not (w.x2 < rx1 or w.x1 > rx2 or w.y2 < ry1 or w.y1 > ry2):
                new_sel.add(w.index)

        self.selected_indices = new_sel
        self.redraw_selection()
        return True

    def handle_release(self, mx: int, my: int) -> bool:
        """Handles mouse release."""
        self.canvas.delete("ocr_marquee")
        self._is_dragging = False
        self._drag_start_xy = None
        self._drag_start_word_idx = None

        if self.selected_indices:
            self.redraw_selection()
        return True

    def handle_motion(self, mx: int, my: int) -> str:
        """Handles mouse motion. Returns cursor: 'xterm', 'hand2', or 'cross'."""
        if self.is_inside_copy_button(mx, my):
            self.canvas.delete(self.TAG_HOVER)
            self._hovered_region_id = None
            return "hand2"

        target_reg = self.hit_test_region(mx, my)
        if target_reg is not None:
            if target_reg.region_id != self._hovered_region_id:
                self._hovered_region_id = target_reg.region_id
                self.canvas.delete(self.TAG_HOVER)
            return "xterm"

        if self._hovered_region_id is not None:
            self._hovered_region_id = None
            self.canvas.delete(self.TAG_HOVER)

        return "cross"

    def handle_double_click(self, mx: int, my: int) -> bool:
        """Double click selects word under cursor."""
        target_w = self.hit_test_word(mx, my)
        if target_w is not None:
            self.select_range(target_w.index, target_w.index)
            return True
        return False

    def handle_triple_click(self, mx: int, my: int) -> bool:
        """Triple click selects entire line under cursor."""
        target_w = self.hit_test_word(mx, my)
        if target_w is not None:
            line_w = [w for w in self.words if w.line_index == target_w.line_index]
            if line_w:
                self.selected_indices = {w.index for w in line_w}
                self.redraw_selection()
                return True
        return False

    def execute_copy(self) -> str:
        """Copies selected text (or all text if none selected) and invokes callback."""
        text = self.get_selected_text()
        if not text:
            text = self.get_all_text()

        if text:
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(text)
                self.root.update()
            except Exception as e:
                self._logger.debug("Tkinter clipboard write error: %s", e)

        if self.on_copy_callback and text:
            self.on_copy_callback(text)

        if self._btn_rect is not None:
            bx1, by1, bx2, by2 = self._btn_rect
            btn_cx = int((bx1 + bx2) / 2)
            self.canvas.delete(self.TAG_FLOAT_BTN)
            self.canvas.create_image(
                bx1,
                by1,
                image=self._btn_photo,
                anchor=tk.NW,
                tags=(self.TAG_LAYER, self.TAG_FLOAT_BTN),
            )
            self.canvas.create_text(
                btn_cx,
                by1 + (by2 - by1) // 2,
                text="✓ Скопировано!",
                fill="#4ade80",
                font=("Segoe UI", 9, "bold"),
                tags=(self.TAG_LAYER, self.TAG_FLOAT_BTN),
            )
            self.root.after(1200, lambda: self.redraw_selection() if self.is_active else None)

        return text
