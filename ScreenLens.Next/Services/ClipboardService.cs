using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Threading;
using System.Windows;
using System.Windows.Media.Imaging;

namespace ScreenLens.Next.Services;

public interface IClipboardService
{
    bool CopyText(string text);
    bool CopyImage(Bitmap bitmap);
}

public class ClipboardService : IClipboardService
{
    public bool CopyText(string text)
    {
        if (string.IsNullOrEmpty(text))
            return false;

        for (int i = 0; i < 3; i++)
        {
            try
            {
                Clipboard.SetText(text);
                return true;
            }
            catch
            {
                Thread.Sleep(30);
            }
        }
        return false;
    }

    public bool CopyImage(Bitmap bitmap)
    {
        if (bitmap == null)
            return false;

        for (int i = 0; i < 3; i++)
        {
            try
            {
                using var ms = new MemoryStream();
                bitmap.Save(ms, ImageFormat.Png);
                ms.Position = 0;

                var bmi = new BitmapImage();
                bmi.BeginInit();
                bmi.StreamSource = ms;
                bmi.CacheOption = BitmapCacheOption.OnLoad;
                bmi.EndInit();
                bmi.Freeze();

                Clipboard.SetImage(bmi);
                return true;
            }
            catch
            {
                Thread.Sleep(30);
            }
        }
        return false;
    }
}
