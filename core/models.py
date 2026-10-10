from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class CaptureArea:
    """Represents a screen rectangular selection area."""

    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def width(self) -> int:
        return abs(self.x2 - self.x1)

    @property
    def height(self) -> int:
        return abs(self.y2 - self.y1)

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        """Normalized bounding box: (left, top, right, bottom)."""
        return (
            min(self.x1, self.x2),
            min(self.y1, self.y2),
            max(self.x1, self.x2),
            max(self.y1, self.y2),
        )

    @property
    def is_valid(self) -> bool:
        return self.width > 2 and self.height > 2


@dataclass(frozen=True)
class ScreenshotResult:
    """Result of screen capture operation."""

    image_path: Path
    area: CaptureArea
    width: int
    height: int


@dataclass(frozen=True)
class OCRResult:
    """Result of OCR processing on captured image."""

    raw_text: str
    cleaned_text: str
    image_path: Path
    language: str = "rus+eng"
    confidence: float = 0.0
    is_empty: bool = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "is_empty", len(self.cleaned_text.strip()) == 0)
