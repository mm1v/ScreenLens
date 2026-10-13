"""Evaluates OCR accuracy on the benchmark suite.

Calculates:
- CER (Character Error Rate)
- WER (Word Error Rate)
- Tech Identifiers Accuracy (%)
- Bounding Box Integrity
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable, Optional
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.models import OCRResult
from core.ocr import WindowsMediaOCREngine

BENCHMARK_DIR = Path(__file__).resolve().parent
DATASET_FILE = BENCHMARK_DIR / "dataset.json"


def levenshtein_distance(s1: str, s2: str) -> int:
    """Computes Levenshtein edit distance between two sequences (chars or tokens)."""
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)

    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    return prev_row[-1]


def compute_cer(ocr_text: str, gt_text: str) -> float:
    """Computes Character Error Rate."""
    if not gt_text:
        return 0.0 if not ocr_text else 1.0
    dist = levenshtein_distance(ocr_text.strip(), gt_text.strip())
    return dist / float(len(gt_text.strip()))


def compute_wer(ocr_text: str, gt_text: str) -> float:
    """Computes Word Error Rate."""
    ocr_words = ocr_text.strip().split()
    gt_words = gt_text.strip().split()
    if not gt_words:
        return 0.0 if not ocr_words else 1.0
    dist = levenshtein_distance(ocr_words, gt_words)
    return dist / float(len(gt_words))


def compute_tech_acc(ocr_text: str, tech_identifiers: list[str]) -> tuple[int, int, float]:
    """Computes accuracy of technical identifiers recognition."""
    if not tech_identifiers:
        return 0, 0, 1.0
    matched = 0
    # Clean whitespace and normalize for checking
    clean_ocr = " " + " ".join(ocr_text.split()) + " "
    for ident in tech_identifiers:
        # Exact substring or word-level presence
        if ident in ocr_text or ident in clean_ocr:
            matched += 1
    total = len(tech_identifiers)
    return matched, total, matched / float(total)


def evaluate_engine(
    engine: Optional[WindowsMediaOCREngine] = None,
    custom_recognize_fn: Optional[Callable[[Path], OCRResult]] = None,
    verbose: bool = True,
) -> dict:
    if engine is None and custom_recognize_fn is None:
        engine = WindowsMediaOCREngine(language="rus+eng")

    recognize_fn = custom_recognize_fn or engine.recognize

    with open(DATASET_FILE, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    results = []
    total_cer_dist = 0
    total_cer_len = 0
    total_wer_dist = 0
    total_wer_len = 0
    total_tech_matched = 0
    total_tech_count = 0
    total_bbox_errors = 0

    for item in dataset:
        img_path = Path(item["image_path"])
        gt_text = item["ground_truth"]
        tech_idents = item["tech_identifiers"]

        with Image.open(img_path) as im:
            im_w, im_h = im.size

        ocr_res = recognize_fn(img_path)
        ocr_text = ocr_res.cleaned_text

        cer = compute_cer(ocr_text, gt_text)
        wer = compute_wer(ocr_text, gt_text)
        m_tech, tot_tech, tech_acc = compute_tech_acc(ocr_text, tech_idents)

        # Check bbox integrity
        bbox_errors = 0
        for line in ocr_res.lines:
            for w in line.get("words", []):
                b = w.get("bounding_rect", {})
                x, y = b.get("x", 0.0), b.get("y", 0.0)
                bw, bh = b.get("width", 0.0), b.get("height", 0.0)
                # Ensure box is non-negative and reasonably within image boundaries (+5px tolerance)
                if x < -5 or y < -5 or bw <= 0 or bh <= 0 or x > im_w + 5 or y > im_h + 5:
                    bbox_errors += 1

        total_cer_dist += levenshtein_distance(ocr_text.strip(), gt_text.strip())
        total_cer_len += len(gt_text.strip())
        total_wer_dist += levenshtein_distance(ocr_text.strip().split(), gt_text.strip().split())
        total_wer_len += len(gt_text.strip().split())
        total_tech_matched += m_tech
        total_tech_count += tot_tech
        total_bbox_errors += bbox_errors

        results.append({
            "id": item["id"],
            "category": item["category"],
            "cer": cer,
            "wer": wer,
            "tech_matched": m_tech,
            "tech_total": tot_tech,
            "tech_acc": tech_acc,
            "bbox_errors": bbox_errors,
            "ocr_text": ocr_text,
            "gt_text": gt_text,
        })

    overall_cer = total_cer_dist / float(total_cer_len) if total_cer_len else 0.0
    overall_wer = total_wer_dist / float(total_wer_len) if total_wer_len else 0.0
    overall_tech_acc = total_tech_matched / float(total_tech_count) if total_tech_count else 1.0

    summary = {
        "overall_cer": overall_cer,
        "overall_wer": overall_wer,
        "overall_tech_acc": overall_tech_acc,
        "total_bbox_errors": total_bbox_errors,
        "items": results,
    }

    if verbose:
        print("\n" + "=" * 90)
        print(f"{'Category / ID':<26} | {'CER (%)':<9} | {'WER (%)':<9} | {'Tech Acc':<12} | {'BBox Ok?'}")
        print("-" * 90)
        for r in results:
            tech_str = f"{r['tech_matched']}/{r['tech_total']} ({r['tech_acc']*100:.1f}%)"
            bbox_str = "YES" if r["bbox_errors"] == 0 else f"ERR({r['bbox_errors']})"
            print(f"{r['id']:<26} | {r['cer']*100:>7.2f}% | {r['wer']*100:>7.2f}% | {tech_str:<12} | {bbox_str}")
        print("-" * 90)
        print(f"{'OVERALL AVERAGE':<26} | {overall_cer*100:>7.2f}% | {overall_wer*100:>7.2f}% | {total_tech_matched}/{total_tech_count} ({overall_tech_acc*100:.1f}%) | {'YES' if total_bbox_errors == 0 else 'ERRORS'}")
        print("=" * 90 + "\n")

    return summary


if __name__ == "__main__":
    evaluate_engine()
