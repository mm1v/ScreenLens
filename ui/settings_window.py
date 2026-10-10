import tkinter as tk
from tkinter import ttk
import webbrowser
from typing import Callable, Optional

from core.clipboard import get_clipboard_text, setup_universal_clipboard_shortcuts
from storage.db import AppSettings, DatabaseManager, set_windows_autostart


class SettingsWindow:
    """GUI window for application settings configuration."""

    def __init__(
        self,
        db: DatabaseManager,
        on_save_callback: Optional[Callable[[AppSettings], None]] = None,
    ) -> None:
        self._db = db
        self._on_save = on_save_callback

    def show(self, master: Optional[tk.Misc] = None) -> None:
        """Displays settings dialog with enabled minimize and close buttons."""
        settings = self._db.get_settings()

        parent = master or (tk._default_root if tk._default_root and getattr(tk._default_root, "winfo_exists", lambda: False)() else None)
        win = tk.Toplevel(parent) if parent else tk.Tk()
        win.title("Настройки ScreenLens")
        win.geometry("490x580")
        win.minsize(460, 520)
        win.resizable(True, True)
        win.configure(bg="#181c24")
        win.attributes("-topmost", True)

        def on_close():
            try:
                win.destroy()
            except Exception:
                pass

        win.protocol("WM_DELETE_WINDOW", on_close)

        # Container
        frame = tk.Frame(win, bg="#181c24", padx=20, pady=16)
        frame.pack(fill=tk.BOTH, expand=True)

        title = tk.Label(
            frame,
            text="⚙️ Настройки ScreenLens",
            font=("Segoe UI", 14, "bold"),
            fg="#00e5ff",
            bg="#181c24",
        )
        title.pack(anchor=tk.W, pady=(0, 16))

        def attach_context_menu(widget: tk.Entry):
            menu = tk.Menu(widget, tearoff=0, bg="#222834", fg="#ffffff", activebackground="#00e5ff", activeforeground="#000000")

            def do_paste():
                val = get_clipboard_text().strip()
                if val:
                    widget.delete(0, tk.END)
                    widget.insert(0, val)

            def do_copy():
                widget.clipboard_clear()
                widget.clipboard_append(widget.get())

            def do_cut():
                widget.clipboard_clear()
                widget.clipboard_append(widget.get())
                widget.delete(0, tk.END)

            def do_select_all():
                widget.select_range(0, tk.END)
                widget.icursor(tk.END)

            menu.add_command(label="📋 Вставить", command=do_paste)
            menu.add_command(label="Копировать", command=do_copy)
            menu.add_command(label="Вырезать", command=do_cut)
            menu.add_separator()
            menu.add_command(label="Выделить всё", command=do_select_all)

            def popup(event):
                widget.focus_set()
                menu.tk_popup(event.x_root, event.y_root)

            widget.bind("<Button-3>", popup)

        # 1. Hotkey
        tk.Label(frame, text="Горячая клавиша захвата:", font=("Segoe UI", 10, "bold"), fg="#ffffff", bg="#181c24").pack(anchor=tk.W)
        hotkey_ent = tk.Entry(frame, bg="#222834", fg="#ffffff", insertbackground="#00e5ff", font=("Consolas", 10), relief=tk.FLAT)
        hotkey_ent.insert(0, settings.hotkey)
        hotkey_ent.pack(fill=tk.X, pady=(2, 10), ipady=3)
        attach_context_menu(hotkey_ent)

        # 2. OCR Language
        tk.Label(frame, text="Язык распознавания (OCR):", font=("Segoe UI", 10, "bold"), fg="#ffffff", bg="#181c24").pack(anchor=tk.W)
        ocr_lang_cb = ttk.Combobox(frame, values=["rus+eng", "rus", "eng"], state="readonly")
        ocr_lang_cb.set(settings.ocr_lang)
        ocr_lang_cb.pack(fill=tk.X, pady=(2, 10))

        # 3. Translation Language
        tk.Label(frame, text="Язык перевода по умолчанию:", font=("Segoe UI", 10, "bold"), fg="#ffffff", bg="#181c24").pack(anchor=tk.W)
        tr_lang_cb = ttk.Combobox(frame, values=["ru", "en", "es", "de", "fr", "zh", "ja"], state="readonly")
        tr_lang_cb.set(settings.translate_lang)
        tr_lang_cb.pack(fill=tk.X, pady=(2, 10))

        # 4. Gemini API Key
        gem_header = tk.Frame(frame, bg="#181c24")
        gem_header.pack(fill=tk.X)
        tk.Label(gem_header, text="Google Gemini API Key:", font=("Segoe UI", 10, "bold"), fg="#ffffff", bg="#181c24").pack(side=tk.LEFT)

        link_lbl = tk.Label(
            gem_header,
            text="(Получить ключ бесплатно)",
            font=("Segoe UI", 9, "underline"),
            fg="#00e5ff",
            bg="#181c24",
            cursor="hand2",
        )
        link_lbl.pack(side=tk.RIGHT)
        link_lbl.bind("<Button-1>", lambda e: webbrowser.open("https://aistudio.google.com/app/apikey"))

        key_frame = tk.Frame(frame, bg="#181c24")
        key_frame.pack(fill=tk.X, pady=(2, 10))

        key_ent = tk.Entry(
            key_frame,
            show="*",
            bg="#222834",
            fg="#ffffff",
            insertbackground="#00e5ff",
            font=("Consolas", 10),
            relief=tk.FLAT,
        )
        key_ent.insert(0, settings.gemini_api_key)
        key_ent.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3)
        key_ent.bind("<Button-1>", lambda e: key_ent.focus_set())
        attach_context_menu(key_ent)

        def paste_key():
            val = get_clipboard_text().strip()
            if val:
                key_ent.delete(0, tk.END)
                key_ent.insert(0, val)
                key_ent.focus_set()

        paste_btn = tk.Button(
            key_frame, text="📋 Вставить", bg="#2c3545", fg="#00e5ff", relief=tk.FLAT,
            font=("Segoe UI", 8, "bold"), command=paste_key, cursor="hand2"
        )
        paste_btn.pack(side=tk.RIGHT, padx=(4, 0))

        def toggle_key():
            if key_ent.cget("show") == "":
                key_ent.config(show="*")
                show_btn.config(text="👁 Показать")
            else:
                key_ent.config(show="")
                show_btn.config(text="🔒 Скрыть")

        show_btn = tk.Button(key_frame, text="👁 Показать", bg="#2c3545", fg="#ffffff", relief=tk.FLAT, font=("Segoe UI", 8), command=toggle_key, cursor="hand2")
        show_btn.pack(side=tk.RIGHT, padx=(4, 0))

        # 5. Checkboxes (Sound, Autostart, HUD, AI)
        ai_var = tk.BooleanVar(value=settings.ai_enabled)
        chk_ai = tk.Checkbutton(
            frame,
            text="Использовать нейросеть Gemini ИИ (умный анализ снимка)",
            variable=ai_var,
            bg="#181c24",
            fg="#00e5ff",
            selectcolor="#222834",
            activebackground="#181c24",
            activeforeground="#00e5ff",
            font=("Segoe UI", 9, "bold"),
        )
        chk_ai.pack(anchor=tk.W, pady=4)

        sound_var = tk.BooleanVar(value=settings.sound_enabled)
        chk_sound = tk.Checkbutton(
            frame,
            text="Звуковое уведомление при успешном захвате",
            variable=sound_var,
            bg="#181c24",
            fg="#ffffff",
            selectcolor="#222834",
            activebackground="#181c24",
            activeforeground="#ffffff",
        )
        chk_sound.pack(anchor=tk.W, pady=4)

        auto_var = tk.BooleanVar(value=settings.autostart)
        chk_auto = tk.Checkbutton(
            frame,
            text="Запускать ScreenLens вместе с Windows (автозагрузка)",
            variable=auto_var,
            bg="#181c24",
            fg="#ffffff",
            selectcolor="#222834",
            activebackground="#181c24",
            activeforeground="#ffffff",
        )
        chk_auto.pack(anchor=tk.W, pady=4)

        hud_var = tk.BooleanVar(value=settings.show_hud)
        chk_hud = tk.Checkbutton(
            frame,
            text="Показывать плавающую панель действий после выделения",
            variable=hud_var,
            bg="#181c24",
            fg="#ffffff",
            selectcolor="#222834",
            activebackground="#181c24",
            activeforeground="#ffffff",
        )
        chk_hud.pack(anchor=tk.W, pady=4)

        # Save Button
        def save():
            new_settings = AppSettings(
                hotkey=hotkey_ent.get().strip(),
                ocr_lang=ocr_lang_cb.get().strip(),
                ocr_engine=settings.ocr_engine,
                translate_lang=tr_lang_cb.get().strip(),
                gemini_api_key=key_ent.get().strip(),
                gemini_model=settings.gemini_model,
                ai_enabled=ai_var.get(),
                sound_enabled=sound_var.get(),
                autostart=auto_var.get(),
                show_hud=hud_var.get(),
            )
            self._db.save_settings(new_settings)
            set_windows_autostart(new_settings.autostart)

            if self._on_save:
                self._on_save(new_settings)

            btn_save.config(text="✓ Настройки сохранены!", bg="#00e676", fg="#181c24")
            win.update_idletasks()
            win.after(350, on_close)

        btn_save = tk.Button(
            frame,
            text="💾 Сохранить настройки",
            font=("Segoe UI", 10, "bold"),
            bg="#00bcd4",
            fg="#ffffff",
            activebackground="#00acc1",
            relief=tk.FLAT,
            command=save,
            cursor="hand2",
        )
        btn_save.pack(fill=tk.X, pady=(16, 0), ipady=6)

        # Bind universal Ctrl+V, Ctrl+C under Russian layout
        setup_universal_clipboard_shortcuts(win)

        win.focus_force()
        if isinstance(win, tk.Toplevel) and parent:
            win.transient(parent)
            win.grab_set()
            parent.wait_window(win)
        else:
            win.mainloop()
