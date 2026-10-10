import os
import shutil
import subprocess
import sys
from pathlib import Path

# Ensure UTF-8 output on Windows console
if sys.platform == "win32":
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def build_executable() -> None:
    """Builds ScreenLens into a standalone single-file Windows executable."""
    base_dir = Path(__file__).resolve().parent
    icon_path = base_dir / "assets" / "icon.ico"
    assets_dir = base_dir / "assets"

    print("=" * 60)
    print("          Сборка ScreenLens в единый EXE (PyInstaller)")
    print("=" * 60)

    pyinstaller_exe = base_dir / ".venv" / "Scripts" / "pyinstaller.exe"
    if not pyinstaller_exe.exists():
        pyinstaller_exe = Path(sys.executable).parent / "pyinstaller.exe"

    if not pyinstaller_exe.exists():
        pyinstaller_exe = Path("pyinstaller")

    # Clean build directories
    for folder in ("build", "dist"):
        p = base_dir / folder
        if p.exists():
            print(f"Очистка директории {folder}...")
            shutil.rmtree(p, ignore_errors=True)

    cmd = [
        str(pyinstaller_exe),
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",  # No console window
        "--name=ScreenLens",
        f"--icon={icon_path}",
        f"--add-data={assets_dir};assets",
        # Package full pynput package and windows backend
        "--collect-all=pynput",
        "--collect-all=pystray",
        "--collect-all=winocr",
        "--collect-submodules=winrt",
        # Hidden imports for Windows and dynamic modules
        "--hidden-import=pystray",
        "--hidden-import=PIL",
        "--hidden-import=winocr",
        "--hidden-import=cv2",
        "--hidden-import=sqlite3",
        "--hidden-import=pyperclip",
        "--hidden-import=logging.handlers",
        "--hidden-import=socks",
        "--hidden-import=sockshandler",
        str(base_dir / "app.py"),
    ]

    print(f"\nВыполнение команды:\n{' '.join(cmd)}\n")
    proc = subprocess.run(cmd, cwd=str(base_dir))

    if proc.returncode == 0:
        exe_path = base_dir / "dist" / "ScreenLens.exe"
        size_mb = exe_path.stat().st_size / (1024 * 1024) if exe_path.exists() else 0
        print("\n" + "=" * 60)
        print("          СБОРКА УСПЕШНО ЗАВЕРШЕНА!")
        print("=" * 60)
        print(f"Готовый исполняемый файл: {exe_path}")
        print(f"Размер: {size_mb:.1f} MB")
        print("Приложение готово к распространению и добавлению в автозагрузку.")
    else:
        print(f"\nОшибка сборки! Код возврата: {proc.returncode}")
        sys.exit(proc.returncode)


if __name__ == "__main__":
    build_executable()
