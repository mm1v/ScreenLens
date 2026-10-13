using System;
using System.Collections.Generic;
using System.Linq;
using System.Windows;

namespace ScreenLens.Next.Core;

/// <summary>
/// Represents a rectangular screen capture area in pixel coordinates.
/// </summary>
public readonly record struct CaptureArea(int X, int Y, int Width, int Height)
{
    public int Left => X;
    public int Top => Y;
    public int Right => X + Width;
    public int Bottom => Y + Height;

    public bool IsValid => Width > 2 && Height > 2;

    public static CaptureArea FromPoints(int x1, int y1, int x2, int y2)
    {
        int left = Math.Min(x1, x2);
        int top = Math.Min(y1, y2);
        int width = Math.Abs(x2 - x1);
        int height = Math.Abs(y2 - y1);
        return new CaptureArea(left, top, width, height);
    }
}

/// <summary>
/// Represents an individual OCR word with its bounding rectangle relative to the captured image.
/// </summary>
public record OcrWord(string Text, Rect BoundingRect, double Confidence = 1.0)
{
    public double X => BoundingRect.X;
    public double Y => BoundingRect.Y;
    public double Width => BoundingRect.Width;
    public double Height => BoundingRect.Height;
    public double Right => BoundingRect.Right;
    public double Bottom => BoundingRect.Bottom;
}

/// <summary>
/// Represents an OCR line containing words grouped in natural reading order.
/// </summary>
public record OcrLine(string Text, Rect BoundingRect, IReadOnlyList<OcrWord> Words)
{
    public double X => BoundingRect.X;
    public double Y => BoundingRect.Y;
    public double Width => BoundingRect.Width;
    public double Height => BoundingRect.Height;
}

/// <summary>
/// Full result of an OCR recognition run.
/// </summary>
public class OcrResult
{
    public string FullText { get; }
    public IReadOnlyList<OcrLine> Lines { get; }
    public IReadOnlyList<OcrWord> AllWords { get; }
    public int ImageWidth { get; }
    public int ImageHeight { get; }
    public bool IsEmpty => string.IsNullOrWhiteSpace(FullText);

    public OcrResult(
        string fullText,
        IReadOnlyList<OcrLine> lines,
        IReadOnlyList<OcrWord> allWords,
        int imageWidth,
        int imageHeight)
    {
        FullText = fullText ?? string.Empty;
        Lines = lines ?? Array.Empty<OcrLine>();
        AllWords = allWords ?? Array.Empty<OcrWord>();
        ImageWidth = imageWidth;
        ImageHeight = imageHeight;
    }

    public static OcrResult Empty(int width = 0, int height = 0) =>
        new(string.Empty, Array.Empty<OcrLine>(), Array.Empty<OcrWord>(), width, height);
}
