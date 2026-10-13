using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Media.Imaging;
using ScreenLens.Next.Core;

namespace ScreenLens.Next.Capture;

public class ScreenCapturer : IDisposable
{
    private const int SRCCOPY = 0x00CC0020;

    [DllImport("gdi32.dll")]
    private static extern bool BitBlt(IntPtr hObject, int nXDest, int nYDest, int nWidth, int nHeight, IntPtr hObjectSource, int nXSrc, int nYSrc, int dwRop);

    [DllImport("gdi32.dll")]
    private static extern IntPtr CreateCompatibleBitmap(IntPtr hDC, int nWidth, int nHeight);

    [DllImport("gdi32.dll")]
    private static extern IntPtr CreateCompatibleDC(IntPtr hDC);

    [DllImport("gdi32.dll")]
    private static extern bool DeleteDC(IntPtr hDC);

    [DllImport("gdi32.dll")]
    private static extern bool DeleteObject(IntPtr hObject);

    [DllImport("gdi32.dll")]
    private static extern IntPtr SelectObject(IntPtr hDC, IntPtr hObject);

    [DllImport("user32.dll")]
    private static extern IntPtr GetDesktopWindow();

    [DllImport("user32.dll")]
    private static extern IntPtr GetWindowDC(IntPtr hWnd);

    [DllImport("user32.dll")]
    private static extern int ReleaseDC(IntPtr hWnd, IntPtr hDC);

    public Rect VirtualBounds { get; private set; }
    public Bitmap? FullScreenshot { get; private set; }

    public Bitmap CaptureVirtualScreen()
    {
        DpiHelper.EnableDpiAwareness();
        VirtualBounds = DpiHelper.GetVirtualScreenPhysicalBounds();

        int x = (int)VirtualBounds.X;
        int y = (int)VirtualBounds.Y;
        int width = (int)VirtualBounds.Width;
        int height = (int)VirtualBounds.Height;

        IntPtr desktopWnd = GetDesktopWindow();
        IntPtr desktopDc = GetWindowDC(desktopWnd);
        IntPtr memoryDc = CreateCompatibleDC(desktopDc);
        IntPtr bitmapHandle = CreateCompatibleBitmap(desktopDc, width, height);
        IntPtr oldBitmap = SelectObject(memoryDc, bitmapHandle);

        try
        {
            BitBlt(memoryDc, 0, 0, width, height, desktopDc, x, y, SRCCOPY);
            SelectObject(memoryDc, oldBitmap);

            FullScreenshot?.Dispose();
            FullScreenshot = Image.FromHbitmap(bitmapHandle);
            return (Bitmap)FullScreenshot.Clone();
        }
        finally
        {
            DeleteObject(bitmapHandle);
            DeleteDC(memoryDc);
            ReleaseDC(desktopWnd, desktopDc);
        }
    }

    public Bitmap Crop(CaptureArea area)
    {
        if (FullScreenshot == null)
            throw new InvalidOperationException("Screen has not been captured yet.");

        // Area is relative to virtual screen origin (X, Y)
        int srcX = Math.Max(0, area.X - (int)VirtualBounds.X);
        int srcY = Math.Max(0, area.Y - (int)VirtualBounds.Y);
        int width = Math.Min(area.Width, FullScreenshot.Width - srcX);
        int height = Math.Min(area.Height, FullScreenshot.Height - srcY);

        if (width <= 0 || height <= 0)
            return new Bitmap(1, 1);

        var rect = new Rectangle(srcX, srcY, width, height);
        return FullScreenshot.Clone(rect, PixelFormat.Format32bppArgb);
    }

    public static BitmapSource ToBitmapSource(Bitmap bitmap)
    {
        using var memory = new MemoryStream();
        bitmap.Save(memory, ImageFormat.Png);
        memory.Position = 0;

        var bitmapImage = new BitmapImage();
        bitmapImage.BeginInit();
        bitmapImage.StreamSource = memory;
        bitmapImage.CacheOption = BitmapCacheOption.OnLoad;
        bitmapImage.EndInit();
        bitmapImage.Freeze();

        return bitmapImage;
    }

    public static Bitmap CreateDimmedBitmap(Bitmap source, float factor = 0.50f)
    {
        var target = new Bitmap(source.Width, source.Height, PixelFormat.Format32bppArgb);
        using var g = Graphics.FromImage(target);

        var colorMatrix = new ColorMatrix(new[]
        {
            new[] { factor, 0f, 0f, 0f, 0f },
            new[] { 0f, factor, 0f, 0f, 0f },
            new[] { 0f, 0f, factor, 0f, 0f },
            new[] { 0f, 0f, 0f, 1f, 0f },
            new[] { 0f, 0f, 0f, 0f, 1f }
        });

        using var attributes = new ImageAttributes();
        attributes.SetColorMatrix(colorMatrix, ColorMatrixFlag.Default, ColorAdjustType.Bitmap);

        g.DrawImage(source,
            new Rectangle(0, 0, source.Width, source.Height),
            0, 0, source.Width, source.Height,
            GraphicsUnit.Pixel,
            attributes);

        return target;
    }

    public void Dispose()
    {
        FullScreenshot?.Dispose();
        FullScreenshot = null;
        GC.SuppressFinalize(this);
    }
}
