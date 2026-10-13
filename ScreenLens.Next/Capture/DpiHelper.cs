using System;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace ScreenLens.Next.Capture;

public static class DpiHelper
{
    private const int SM_XVIRTUALSCREEN = 76;
    private const int SM_YVIRTUALSCREEN = 77;
    private const int SM_CXVIRTUALSCREEN = 78;
    private const int SM_CYVIRTUALSCREEN = 79;

    [DllImport("user32.dll")]
    private static extern int GetSystemMetrics(int nIndex);

    [DllImport("user32.dll")]
    public static extern bool SetProcessDPIAware();

    [DllImport("shcore.dll")]
    private static extern int SetProcessDpiAwareness(int awareness);

    public static void EnableDpiAwareness()
    {
        try
        {
            // 2 = PROCESS_PER_MONITOR_DPI_AWARE
            SetProcessDpiAwareness(2);
        }
        catch
        {
            try
            {
                SetProcessDPIAware();
            }
            catch
            {
                // Fallback ignored
            }
        }
    }

    public static Rect GetVirtualScreenPhysicalBounds()
    {
        int x = GetSystemMetrics(SM_XVIRTUALSCREEN);
        int y = GetSystemMetrics(SM_YVIRTUALSCREEN);
        int width = GetSystemMetrics(SM_CXVIRTUALSCREEN);
        int height = GetSystemMetrics(SM_CYVIRTUALSCREEN);

        if (width <= 0 || height <= 0)
        {
            width = (int)SystemParameters.PrimaryScreenWidth;
            height = (int)SystemParameters.PrimaryScreenHeight;
            x = 0;
            y = 0;
        }

        return new Rect(x, y, width, height);
    }

    public static Rect PhysicalToDip(Rect physicalRect, double dpiScaleX, double dpiScaleY)
    {
        double scaleX = dpiScaleX > 0 ? dpiScaleX : 1.0;
        double scaleY = dpiScaleY > 0 ? dpiScaleY : 1.0;

        return new Rect(
            physicalRect.X / scaleX,
            physicalRect.Y / scaleY,
            physicalRect.Width / scaleX,
            physicalRect.Height / scaleY
        );
    }

    public static Rect DipToPhysical(Rect dipRect, double dpiScaleX, double dpiScaleY)
    {
        double scaleX = dpiScaleX > 0 ? dpiScaleX : 1.0;
        double scaleY = dpiScaleY > 0 ? dpiScaleY : 1.0;

        return new Rect(
            dipRect.X * scaleX,
            dipRect.Y * scaleY,
            dipRect.Width * scaleX,
            dipRect.Height * scaleY
        );
    }

    public static Point DipToPhysical(Point dipPoint, double dpiScaleX, double dpiScaleY)
    {
        double scaleX = dpiScaleX > 0 ? dpiScaleX : 1.0;
        double scaleY = dpiScaleY > 0 ? dpiScaleY : 1.0;

        return new Point(dipPoint.X * scaleX, dipPoint.Y * scaleY);
    }

    public static Point PhysicalToDip(Point physicalPoint, double dpiScaleX, double dpiScaleY)
    {
        double scaleX = dpiScaleX > 0 ? dpiScaleX : 1.0;
        double scaleY = dpiScaleY > 0 ? dpiScaleY : 1.0;

        return new Point(physicalPoint.X / scaleX, physicalPoint.Y / scaleY);
    }
}
