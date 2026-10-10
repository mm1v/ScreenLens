import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys


def setup_logger(name: str = "screenlens", level: int = logging.INFO) -> logging.Logger:
    """Configures and returns an application logger with console and file output."""
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        formatter = logging.Formatter(
            fmt="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # 1. Console handler
        try:
            c_handler = logging.StreamHandler(sys.stdout)
            c_handler.setLevel(level)
            c_handler.setFormatter(formatter)
            logger.addHandler(c_handler)
        except Exception:
            pass

        # 2. Rotating file handler in AppData
        try:
            if sys.platform == "win32":
                base_dir = Path(os.environ.get("APPDATA", Path.home())) / "ScreenLens"
            else:
                base_dir = Path.home() / ".config" / "ScreenLens"
            base_dir.mkdir(parents=True, exist_ok=True)
            log_file = base_dir / "screenlens.log"

            f_handler = RotatingFileHandler(
                log_file,
                maxBytes=2 * 1024 * 1024,  # 2 MB
                backupCount=3,
                encoding="utf-8",
            )
            f_handler.setLevel(level)
            f_handler.setFormatter(formatter)
            logger.addHandler(f_handler)
        except Exception:
            pass

    return logger

