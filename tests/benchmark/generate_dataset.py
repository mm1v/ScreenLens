"""Generates a reproducible suite of benchmark test screenshots with ground truth.

Covers:
1. Terminal / Git commits with code, hashes, flags, underscores, plus signs.
2. Technical reports with tables, percentages, decimal numbers, test names.
3. Low contrast dark mode text (IDE comments, metadata).
4. Low contrast light mode text (subdued UI captions).
5. UI dialog with icons / badges alongside text.
6. Code snippet with mathematical symbols, brackets, type annotations.
"""

from __future__ import annotations

import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

BENCHMARK_DIR = Path(__file__).resolve().parent
IMAGES_DIR = BENCHMARK_DIR / "images"
DATASET_FILE = BENCHMARK_DIR / "dataset.json"

FONTS_DIR = Path("C:/Windows/Fonts")
FONT_CONSOLAS = str(FONTS_DIR / "consola.ttf")
FONT_SEGOEUI = str(FONTS_DIR / "segoeui.ttf")
FONT_ARIAL = str(FONTS_DIR / "arial.ttf")


DATASET = [
    {
        "id": "1_terminal_git",
        "category": "terminal_git",
        "description": "Terminal git log with commit hashes, branch, underscores, plus sign, brackets",
        "font_path": FONT_CONSOLAS,
        "font_size": 14,
        "bg_color": (30, 30, 30),
        "text_color": (220, 220, 220),
        "lines": [
            'b8a9c0d git commit -m "fix(core): update _merge_dual_language_structured()"',
            '* 3a0f12c feat(hud): add + button for zoom_in [100% done]',
            'Status: 0 errors, 3 warnings in branch feat/ocr_v2',
        ],
        "tech_identifiers": [
            "b8a9c0d",
            "_merge_dual_language_structured",
            "3a0f12c",
            "+",
            "100%",
            "0",
            "3",
            "feat/ocr_v2",
        ],
    },
    {
        "id": "2_tech_report_table",
        "category": "tech_report_table",
        "description": "Technical report with comparison table, percentages, zeroes, and test names",
        "font_path": FONT_SEGOEUI,
        "font_size": 13,
        "bg_color": (24, 26, 30),
        "text_color": (240, 240, 240),
        "lines": [
            "Таблица сравнения ДО и ПОСЛЕ:",
            "Character Error Rate (CER): 0.23% (расстояние 3) vs 35.41% (расстояние 466)",
            "Точные совпадения тестов: 10 / 10 (100%)",
            "1. test_variant_a_single_line_cohesive_region — OK (0.12s)",
            "2. test_variant_a_horizontal_gap_splits_regions — OK (0.05s)",
            "3. test_variant_a_stable_height_across_jittery_word_boxes — OK (0.08s)",
        ],
        "tech_identifiers": [
            "0.23%",
            "35.41%",
            "10 / 10",
            "100%",
            "test_variant_a_single_line_cohesive_region",
            "test_variant_a_horizontal_gap_splits_regions",
            "test_variant_a_stable_height_across_jittery_word_boxes",
            "0.12s",
            "0.05s",
            "0.08s",
        ],
    },
    {
        "id": "3_low_contrast_dark",
        "category": "low_contrast_dark",
        "description": "Low-contrast IDE dark theme status logs with gray text",
        "font_path": FONT_CONSOLAS,
        "font_size": 13,
        "bg_color": (30, 30, 30),
        "text_color": (120, 126, 136),  # Low contrast gray on dark
        "lines": [
            "2026-10-13 14:02:59 [INFO] worker_thread_0 initialized",
            "DEBUG: memory_usage = 48.5 MB, cache_hit_ratio = 98.2%",
            "TODO: refactor _normalize_intra_word_homoglyphs() before release",
        ],
        "tech_identifiers": [
            "2026-10-13",
            "[INFO]",
            "worker_thread_0",
            "48.5",
            "98.2%",
            "_normalize_intra_word_homoglyphs",
        ],
    },
    {
        "id": "4_low_contrast_light",
        "category": "low_contrast_light",
        "description": "Low-contrast light theme secondary metadata with pale gray text",
        "font_path": FONT_ARIAL,
        "font_size": 13,
        "bg_color": (248, 249, 250),
        "text_color": (145, 150, 160),  # Low contrast gray on light
        "lines": [
            "ID: #8042 | Версия: v2.4.0 (сборка 1030)",
            "Статус: Завершено успешно (+100% квоты)",
            "Примечание: 0 из 3 запросов заблокировано",
        ],
        "tech_identifiers": [
            "#8042",
            "v2.4.0",
            "1030",
            "+100%",
            "0",
            "3",
        ],
    },
    {
        "id": "5_ui_icons_small",
        "category": "ui_icons_small",
        "description": "Small UI text with file icons and badges alongside lines",
        "font_path": FONT_SEGOEUI,
        "font_size": 12,
        "bg_color": (32, 34, 38),
        "text_color": (230, 230, 230),
        "has_icons": True,
        "lines": [
            "main.py (140 строк, 0 предупреждений)",
            "settings_window.py (изменено +5 строк)",
            "database_v2.db (размер: 512 КБ, квота 50%)",
        ],
        "tech_identifiers": [
            "main.py",
            "0",
            "settings_window.py",
            "+5",
            "database_v2.db",
            "50%",
        ],
    },
    {
        "id": "6_code_symbols_math",
        "category": "code_symbols_math",
        "description": "Code snippet with math brackets, plus, equals, type arrows and zeroes",
        "font_path": FONT_CONSOLAS,
        "font_size": 14,
        "bg_color": (37, 37, 38),
        "text_color": (220, 220, 220),
        "lines": [
            "def calculate_offset(x: int, y: int = 0) -> float:",
            "    ratio = (x + 10) / [y * 2.5] if y != 0 else 0.0",
            "    return ratio * 100.0  # +0.5% tolerance",
        ],
        "tech_identifiers": [
            "calculate_offset",
            "x: int",
            "y: int = 0",
            "-> float:",
            "(x + 10)",
            "[y * 2.5]",
            "!= 0",
            "0.0",
            "100.0",
            "+0.5%",
        ],
    },
]


def render_item(item: dict) -> Path:
    font = ImageFont.truetype(item["font_path"], item["font_size"])
    pad_x, pad_y = 20, 16
    line_spacing = 8

    # Determine canvas size
    dummy_img = Image.new("RGB", (100, 100))
    dummy_draw = ImageDraw.Draw(dummy_img)

    icon_w = 22 if item.get("has_icons") else 0
    max_w = 0
    total_h = pad_y * 2

    line_heights = []
    for line in item["lines"]:
        bbox = dummy_draw.textbbox((0, 0), line, font=font)
        lw = bbox[2] - bbox[0] + icon_w
        lh = bbox[3] - bbox[1]
        max_w = max(max_w, lw)
        line_heights.append(lh)
        total_h += lh + line_spacing
    total_h -= line_spacing

    img_w = max_w + pad_x * 2 + 10
    img_h = total_h

    img = Image.new("RGB", (img_w, img_h), color=item["bg_color"])
    draw = ImageDraw.Draw(img)

    curr_y = pad_y
    for idx, (line, lh) in enumerate(zip(item["lines"], line_heights)):
        text_x = pad_x
        if item.get("has_icons"):
            # Render a UI icon badge on the left
            icon_cx = pad_x + 6
            icon_cy = curr_y + lh // 2
            if idx == 0:
                # Circle badge
                draw.ellipse([icon_cx - 5, icon_cy - 5, icon_cx + 5, icon_cy + 5], fill=(80, 160, 255))
            elif idx == 1:
                # Square badge
                draw.rectangle([icon_cx - 5, icon_cy - 5, icon_cx + 5, icon_cy + 5], fill=(255, 180, 60))
            else:
                # Folder/document glyph
                draw.polygon([(icon_cx - 5, icon_cy + 5), (icon_cx + 5, icon_cy + 5), (icon_cx, icon_cy - 5)], fill=(100, 220, 120))
            text_x += icon_w

        draw.text((text_x, curr_y), line, fill=item["text_color"], font=font)
        curr_y += lh + line_spacing

    out_path = IMAGES_DIR / f"{item['id']}.png"
    img.save(out_path)
    return out_path


def main() -> None:
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []
    for item in DATASET:
        p = render_item(item)
        entry = {
            "id": item["id"],
            "category": item["category"],
            "description": item["description"],
            "image_path": str(p),
            "ground_truth": "\n".join(item["lines"]),
            "tech_identifiers": item["tech_identifiers"],
        }
        manifest.append(entry)

    with open(DATASET_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"Generated {len(manifest)} benchmark images in {IMAGES_DIR}")


if __name__ == "__main__":
    main()
