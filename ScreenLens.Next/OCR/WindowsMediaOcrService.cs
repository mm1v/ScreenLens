using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Windows.Globalization;
using Windows.Graphics.Imaging;
using Windows.Storage.Streams;
using WinOcrEngine = Windows.Media.Ocr.OcrEngine;
using ScreenLens.Next.Core;

namespace ScreenLens.Next.OCR;

public class WindowsMediaOcrService : IOcrService
{
    private WinOcrEngine? _primaryEngine;
    private WinOcrEngine? _secondaryEngine;

    public WindowsMediaOcrService()
    {
        InitializeEngines();
    }

    private void InitializeEngines()
    {
        try
        {
            var ruLang = new Language("ru");
            if (WinOcrEngine.IsLanguageSupported(ruLang))
            {
                _primaryEngine = WinOcrEngine.TryCreateFromLanguage(ruLang);
            }

            var enLang = new Language("en-US");
            if (WinOcrEngine.IsLanguageSupported(enLang))
            {
                _secondaryEngine = WinOcrEngine.TryCreateFromLanguage(enLang);
            }

            _primaryEngine ??= WinOcrEngine.TryCreateFromUserProfileLanguages();
            _primaryEngine ??= _secondaryEngine;
        }
        catch
        {
            // Fallback: will return empty result if no engine available
        }
    }

    public async Task<OcrResult> RecognizeAsync(Bitmap bitmap, string language = "ru", CancellationToken ct = default)
    {
        if (bitmap == null || bitmap.Width <= 2 || bitmap.Height <= 2)
            return OcrResult.Empty(bitmap?.Width ?? 0, bitmap?.Height ?? 0);

        var engine = (language.StartsWith("en", StringComparison.OrdinalIgnoreCase) && _secondaryEngine != null)
            ? _secondaryEngine
            : (_primaryEngine ?? _secondaryEngine);

        if (engine == null)
            return OcrResult.Empty(bitmap.Width, bitmap.Height);

        try
        {
            using var ms = new MemoryStream();
            bitmap.Save(ms, ImageFormat.Bmp);
            ms.Position = 0;

            var randomAccessStream = ms.AsRandomAccessStream();
            var decoder = await BitmapDecoder.CreateAsync(randomAccessStream).AsTask(ct);
            var softwareBitmap = await decoder.GetSoftwareBitmapAsync(BitmapPixelFormat.Bgra8, BitmapAlphaMode.Premultiplied).AsTask(ct);

            var winResult = await engine.RecognizeAsync(softwareBitmap).AsTask(ct);

            var allWords = new List<OcrWord>();
            var lines = new List<OcrLine>();

            foreach (var winLine in winResult.Lines)
            {
                var lineWords = new List<OcrWord>();
                foreach (var winWord in winLine.Words)
                {
                    var wordRect = new System.Windows.Rect(
                        winWord.BoundingRect.X,
                        winWord.BoundingRect.Y,
                        winWord.BoundingRect.Width,
                        winWord.BoundingRect.Height
                    );
                    var word = new OcrWord(winWord.Text, wordRect);
                    lineWords.Add(word);
                    allWords.Add(word);
                }

                var lineRect = new System.Windows.Rect(
                    lineWords.Count > 0 ? lineWords.Min(w => w.X) : 0,
                    lineWords.Count > 0 ? lineWords.Min(w => w.Y) : 0,
                    lineWords.Count > 0 ? lineWords.Max(w => w.Right) - lineWords.Min(w => w.X) : 0,
                    lineWords.Count > 0 ? lineWords.Max(w => w.Bottom) - lineWords.Min(w => w.Y) : 0
                );

                lines.Add(new OcrLine(winLine.Text, lineRect, lineWords));
            }

            string fullText = string.Join(Environment.NewLine, lines.Select(l => l.Text));
            return new OcrResult(fullText, lines, allWords, bitmap.Width, bitmap.Height);
        }
        catch (OperationCanceledException)
        {
            return OcrResult.Empty(bitmap.Width, bitmap.Height);
        }
        catch (Exception ex)
        {
            return new OcrResult($"[OCR Error: {ex.Message}]", Array.Empty<OcrLine>(), Array.Empty<OcrWord>(), bitmap.Width, bitmap.Height);
        }
    }
}
