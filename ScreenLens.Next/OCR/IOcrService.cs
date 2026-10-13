using System.Drawing;
using System.Threading;
using System.Threading.Tasks;
using ScreenLens.Next.Core;

namespace ScreenLens.Next.OCR;

public interface IOcrService
{
    Task<OcrResult> RecognizeAsync(Bitmap bitmap, string language = "ru", CancellationToken ct = default);
}
