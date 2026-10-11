import os
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from typing import Optional

from PIL import Image, ImageTk

from core.clipboard import ClipboardManager
from storage.db import DatabaseManager, HistoryItem


class HistoryWindow:
    """GUI window for browsing screen capture history with thumbnails and details."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db
        self._items: list[HistoryItem] = []
        self._current_photo: Optional[ImageTk.PhotoImage] = None
        self._clipboard = ClipboardManager()

    def show(self, master: Optional[tk.Misc] = None) -> None:
        """Displays history window."""
        parent = master or (tk._default_root if tk._default_root and getattr(tk._default_root, "winfo_exists", lambda: False)() else None)
        win = tk.Toplevel(parent) if parent else tk.Tk()
        win.title("История захватов — ScreenLens")
        win.geometry("860x540")
        win.minsize(700, 450)
        win.configure(bg="#181c24")
        win.attributes("-topmost", True)

        def on_close():
            try:
                win.destroy()
            except Exception:
                pass

        win.protocol("WM_DELETE_WINDOW", on_close)

        # Layout: PanedWindow with left list and right preview
        paned = tk.PanedWindow(win, orient=tk.HORIZONTAL, bg="#12151c", sashwidth=4)
        paned.pack(fill=tk.BOTH, expand=True)

        # Left pane (List)
        left_frame = tk.Frame(paned, bg="#181c24", width=320)
        paned.add(left_frame)

        left_header = tk.Frame(left_frame, bg="#12151c", pady=6, padx=8)
        left_header.pack(fill=tk.X)
        tk.Label(left_header, text="📜 Недавние снимки", font=("Segoe UI", 10, "bold"), fg="#00e5ff", bg="#12151c").pack(side=tk.LEFT)

        btn_clear = tk.Button(
            left_header,
            text="🗑 Очистить",
            font=("Segoe UI", 8),
            bg="#d32f2f",
            fg="#ffffff",
            relief=tk.FLAT,
            command=lambda: self._confirm_clear(win, tree),
        )
        btn_clear.pack(side=tk.RIGHT)

        tree = ttk.Treeview(left_frame, columns=("time", "preview"), show="headings", selectmode="browse")
        tree.heading("time", text="Время")
        tree.heading("preview", text="Текст")
        tree.column("time", width=120, stretch=False)
        tree.column("preview", width=180, stretch=True)
        tree.pack(fill=tk.BOTH, expand=True)

        # Right pane (Details)
        right_frame = tk.Frame(paned, bg="#181c24", padx=12, pady=10)
        paned.add(right_frame)

        lbl_details_title = tk.Label(
            right_frame,
            text="Детали записи",
            font=("Segoe UI", 11, "bold"),
            fg="#ffffff",
            bg="#181c24",
        )
        lbl_details_title.pack(anchor=tk.W)

        # Thumbnail canvas
        img_canvas = tk.Label(right_frame, bg="#12151c", text="[Превью изображения]", fg="#6c7a89", height=8)
        img_canvas.pack(fill=tk.X, pady=(6, 8))

        # Details Notebook
        nb = ttk.Notebook(right_frame)
        nb.pack(fill=tk.BOTH, expand=True)

        # Tab: OCR
        tab_ocr = tk.Frame(nb, bg="#1e2430")
        nb.add(tab_ocr, text="Текст (OCR)")
        txt_ocr = tk.Text(tab_ocr, wrap=tk.WORD, bg="#1e2430", fg="#ffffff", font=("Segoe UI", 9), relief=tk.FLAT)
        txt_ocr.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Tab: Translation
        tab_tr = tk.Frame(nb, bg="#1e2430")
        nb.add(tab_tr, text="Перевод")
        txt_tr = tk.Text(tab_tr, wrap=tk.WORD, bg="#1e2430", fg="#ffffff", font=("Segoe UI", 9), relief=tk.FLAT)
        txt_tr.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Tab: Gemini
        tab_gemini = tk.Frame(nb, bg="#1e2430")
        nb.add(tab_gemini, text="ИИ (Gemini)")
        txt_gemini = tk.Text(tab_gemini, wrap=tk.WORD, bg="#1e2430", fg="#ffffff", font=("Segoe UI", 9), relief=tk.FLAT)
        txt_gemini.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Action bar
        act_bar = tk.Frame(right_frame, bg="#181c24", pady=8)
        act_bar.pack(fill=tk.X)

        current_item_path = [None]

        def copy_current_text():
            text = txt_ocr.get("1.0", tk.END).strip()
            if text:
                self._clipboard.copy(text)
                btn_copy.config(text="✓ Скопировано!")
                win.after(1200, lambda: btn_copy.config(text="📋 Скопировать текст"))

        def open_file_folder():
            path_str = current_item_path[0]
            if path_str and Path(path_str).exists():
                subprocess.Popen(f'explorer /select,"{Path(path_str).resolve()}"')

        btn_copy = tk.Button(
            act_bar,
            text="📋 Скопировать текст",
            bg="#00bcd4",
            fg="#ffffff",
            font=("Segoe UI", 9, "bold"),
            relief=tk.FLAT,
            command=copy_current_text,
        )
        btn_copy.pack(side=tk.LEFT, padx=(0, 6))

        btn_open = tk.Button(
            act_bar,
            text="📁 Показать в папке",
            bg="#2c3545",
            fg="#ffffff",
            font=("Segoe UI", 9),
            relief=tk.FLAT,
            command=open_file_folder,
        )
        btn_open.pack(side=tk.LEFT)

        def on_item_selected(event):
            selected = tree.selection()
            if not selected:
                return
            idx = int(selected[0])
            if idx >= len(self._items):
                return
            item = self._items[idx]
            current_item_path[0] = item.image_path

            # Update text tabs
            txt_ocr.delete("1.0", tk.END)
            txt_ocr.insert(tk.END, item.ocr_text or "[Пусто]")

            txt_tr.delete("1.0", tk.END)
            txt_tr.insert(tk.END, item.translated_text or "[Перевод отсутствует]")

            txt_gemini.delete("1.0", tk.END)
            txt_gemini.insert(tk.END, item.gemini_result or "[Анализ Gemini не проводился]")

            # Update thumbnail
            img_p = Path(item.image_path)
            if img_p.exists():
                try:
                    with Image.open(img_p) as raw_img:
                        thumb = raw_img.copy()
                        thumb.thumbnail((450, 110))
                        photo = ImageTk.PhotoImage(thumb)
                        self._current_photo = photo
                        img_canvas.config(image=photo, text="")
                except Exception:
                    img_canvas.config(image="", text="[Ошибка загрузки изображения]")
            else:
                img_canvas.config(image="", text="[Файл изображения удалён]")

        tree.bind("<<TreeviewSelect>>", on_item_selected)

        # Load items
        self._load_items(tree)

        win.bind("<Escape>", lambda e: on_close())
        win.deiconify()
        win.lift()
        win.focus_force()
        if not parent:
            win.mainloop()
        return win

    def _load_items(self, tree: ttk.Treeview) -> None:
        """Populates Treeview with records from DB."""
        for item in tree.get_children():
            tree.delete(item)

        self._items = self._db.get_history(limit=100)
        for i, item in enumerate(self._items):
            preview = (item.ocr_text or item.qr_data or "").replace("\n", " ")[:30]
            tree.insert("", tk.END, iid=str(i), values=(item.timestamp[11:], preview))

        if self._items:
            tree.selection_set("0")

    def _confirm_clear(self, win: tk.Toplevel, tree: ttk.Treeview) -> None:
        if messagebox.askyesno("Очистить историю", "Вы уверены, что хотите удалить всю историю захватов?"):
            self._db.clear_history()
            self._load_items(tree)
