using System;
using System.Drawing;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Shapes;
using ScreenLens.Next.Capture;
using ScreenLens.Next.Core;
using ScreenLens.Next.OCR;
using ScreenLens.Next.Services;

namespace ScreenLens.Next.Overlay;

public partial class SelectionOverlayWindow : Window
{
    private enum DragMode { None, Creating, Moving, Resizing }
    private enum HandleType { None, NW, N, NE, E, SE, S, SW, W, Move }

    private readonly ScreenCapturer _capturer;
    private readonly IOcrService _ocrService;
    private readonly ILensSearchService _lensService;
    private readonly ITranslateService _translateService;
    private readonly IAiService _aiService;
    private readonly IClipboardService _clipboardService;
    private readonly AppSettings _settings;

    private Bitmap? _fullScreenshot;
    private BitmapSource? _fullBitmapSource;
    private Rect _physicalVirtualBounds;
    private double _dpiScaleX = 1.0;
    private double _dpiScaleY = 1.0;

    private Rect _selRect;
    private DragMode _currentDragMode = DragMode.None;
    private HandleType _activeHandle = HandleType.None;
    private System.Windows.Point _dragStartPoint;
    private Rect _origBox;
    private OcrResult? _cachedOcrResult;

    public SelectionOverlayWindow(
        ScreenCapturer capturer,
        IOcrService ocrService,
        ILensSearchService lensService,
        ITranslateService translateService,
        IAiService aiService,
        IClipboardService clipboardService,
        AppSettings settings)
    {
        InitializeComponent();

        _capturer = capturer;
        _ocrService = ocrService;
        _lensService = lensService;
        _translateService = translateService;
        _aiService = aiService;
        _clipboardService = clipboardService;
        _settings = settings;

        Loaded += OnLoaded;
        KeyDown += OnKeyDown;

        // Hook HUD actions
        HudControl.SearchClicked += async (s, e) => await OnSearchActionAsync();
        HudControl.TextClicked += async (s, e) => await OnTextActionAsync();
        HudControl.TranslateClicked += async (s, e) => await OnTranslateActionAsync();
        HudControl.AiClicked += async (s, e) => await OnAiActionAsync();
        HudControl.CopyClicked += (s, e) => OnCopyAction();
        HudControl.CloseClicked += (s, e) => CloseOverlay();

        OcrTextLayer.TextCopied += (s, text) =>
        {
            DimensionText.Text = "✓ Текст скопирован!";
            DimensionBadge.Visibility = Visibility.Visible;
            PositionBadge();
        };

        OverlayCanvas.MouseDown += OnCanvasMouseDown;
        OverlayCanvas.MouseMove += OnCanvasMouseMove;
        OverlayCanvas.MouseUp += OnCanvasMouseUp;
    }

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        var dpi = VisualTreeHelper.GetDpi(this);
        _dpiScaleX = dpi.DpiScaleX;
        _dpiScaleY = dpi.DpiScaleY;

        _physicalVirtualBounds = DpiHelper.GetVirtualScreenPhysicalBounds();

        // Position window across all monitors in DIPs
        Left = _physicalVirtualBounds.X / _dpiScaleX;
        Top = _physicalVirtualBounds.Y / _dpiScaleY;
        Width = _physicalVirtualBounds.Width / _dpiScaleX;
        Height = _physicalVirtualBounds.Height / _dpiScaleY;

        // Capture full virtual desktop
        _fullScreenshot = _capturer.CaptureVirtualScreen();
        _fullBitmapSource = ScreenCapturer.ToBitmapSource(_fullScreenshot);

        // Prepare dimmed background
        using var dimmed = ScreenCapturer.CreateDimmedBitmap(_fullScreenshot, 0.45f);
        DimmedBackgroundImage.Source = ScreenCapturer.ToBitmapSource(dimmed);

        // Prepare bright cutout image source
        BrightCutoutImage.Source = _fullBitmapSource;
        BrightCutoutImage.Width = Width;
        BrightCutoutImage.Height = Height;

        Focus();
    }

    private void OnKeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Escape)
        {
            CloseOverlay();
            e.Handled = true;
        }
    }

    private void CloseOverlay()
    {
        Close();
    }

    #region Mouse Interactions (Selection, Moving, 8-Handle Resizing)

    private HandleType HitTest(System.Windows.Point pt, Rect box, double handleRadius = 10, double edgeMargin = 6)
    {
        if (box.IsEmpty || box.Width <= 0 || box.Height <= 0)
            return HandleType.None;

        var handles = new[]
        {
            (HandleType.NW, new System.Windows.Point(box.Left, box.Top)),
            (HandleType.N,  new System.Windows.Point(box.Left + box.Width / 2.0, box.Top)),
            (HandleType.NE, new System.Windows.Point(box.Right, box.Top)),
            (HandleType.E,  new System.Windows.Point(box.Right, box.Top + box.Height / 2.0)),
            (HandleType.SE, new System.Windows.Point(box.Right, box.Bottom)),
            (HandleType.S,  new System.Windows.Point(box.Left + box.Width / 2.0, box.Bottom)),
            (HandleType.SW, new System.Windows.Point(box.Left, box.Bottom)),
            (HandleType.W,  new System.Windows.Point(box.Left, box.Top + box.Height / 2.0)),
        };

        // 1. Check circular handles first
        foreach (var (type, hPt) in handles)
        {
            if ((pt - hPt).Length <= handleRadius)
                return type;
        }

        // 2. Check edges
        if (pt.X >= box.Left && pt.X <= box.Right)
        {
            if (Math.Abs(pt.Y - box.Top) <= edgeMargin) return HandleType.N;
            if (Math.Abs(pt.Y - box.Bottom) <= edgeMargin) return HandleType.S;
        }
        if (pt.Y >= box.Top && pt.Y <= box.Bottom)
        {
            if (Math.Abs(pt.X - box.Left) <= edgeMargin) return HandleType.W;
            if (Math.Abs(pt.X - box.Right) <= edgeMargin) return HandleType.E;
        }

        // 3. Check inside
        if (box.Contains(pt))
            return HandleType.Move;

        return HandleType.None;
    }

    private Cursor GetCursorForHandle(HandleType handle) => handle switch
    {
        HandleType.NW or HandleType.SE => Cursors.SizeNWSE,
        HandleType.NE or HandleType.SW => Cursors.SizeNESW,
        HandleType.N or HandleType.S => Cursors.SizeNS,
        HandleType.W or HandleType.E => Cursors.SizeWE,
        HandleType.Move => Cursors.SizeAll,
        _ => Cursors.Cross
    };

    private void OnCanvasMouseDown(object sender, MouseButtonEventArgs e)
    {
        if (e.LeftButton != MouseButtonState.Pressed) return;
        var pt = e.GetPosition(OverlayCanvas);

        _dragStartPoint = pt;
        _origBox = _selRect;

        if (!_selRect.IsEmpty && _selRect.Width > 5 && _selRect.Height > 5)
        {
            var hit = HitTest(pt, _selRect);
            if (hit == HandleType.Move)
            {
                _currentDragMode = DragMode.Moving;
                _activeHandle = HandleType.Move;
                HudControl.Visibility = Visibility.Collapsed;
                ResultCard.Visibility = Visibility.Collapsed;
                OcrTextLayer.Visibility = Visibility.Collapsed;
                OverlayCanvas.CaptureMouse();
                return;
            }
            if (hit != HandleType.None)
            {
                _currentDragMode = DragMode.Resizing;
                _activeHandle = hit;
                HudControl.Visibility = Visibility.Collapsed;
                ResultCard.Visibility = Visibility.Collapsed;
                OcrTextLayer.Visibility = Visibility.Collapsed;
                OverlayCanvas.CaptureMouse();
                return;
            }
        }

        // Clicked outside existing box: start fresh creation drag
        _currentDragMode = DragMode.Creating;
        _activeHandle = HandleType.None;
        _selRect = new Rect(pt, pt);
        HudControl.Visibility = Visibility.Collapsed;
        ResultCard.Visibility = Visibility.Collapsed;
        OcrTextLayer.Visibility = Visibility.Collapsed;
        _cachedOcrResult = null;
        OverlayCanvas.CaptureMouse();
    }

    private void OnCanvasMouseMove(object sender, MouseEventArgs e)
    {
        var pt = e.GetPosition(OverlayCanvas);

        // Update cursor on hover when not dragging
        if (_currentDragMode == DragMode.None)
        {
            var hit = HitTest(pt, _selRect);
            Cursor = GetCursorForHandle(hit);
            return;
        }

        double minWidth = 20;
        double minHeight = 20;

        if (_currentDragMode == DragMode.Creating)
        {
            double x = Math.Min(_dragStartPoint.X, pt.X);
            double y = Math.Min(_dragStartPoint.Y, pt.Y);
            double w = Math.Abs(pt.X - _dragStartPoint.X);
            double h = Math.Abs(pt.Y - _dragStartPoint.Y);

            _selRect = new Rect(x, y, w, h);
            UpdateSelectionVisuals(showHandles: false);
            ShowDimensionBadge($"{(int)(w * _dpiScaleX)} × {(int)(h * _dpiScaleY)} px");
        }
        else if (_currentDragMode == DragMode.Moving)
        {
            double dx = pt.X - _dragStartPoint.X;
            double dy = pt.Y - _dragStartPoint.Y;

            double newX = Math.Max(0, Math.Min(ActualWidth - _origBox.Width, _origBox.X + dx));
            double newY = Math.Max(0, Math.Min(ActualHeight - _origBox.Height, _origBox.Y + dy));

            _selRect = new Rect(newX, newY, _origBox.Width, _origBox.Height);
            UpdateSelectionVisuals(showHandles: true);

            int physX = (int)(_physicalVirtualBounds.X + newX * _dpiScaleX);
            int physY = (int)(_physicalVirtualBounds.Y + newY * _dpiScaleY);
            ShowDimensionBadge($"X: {physX}, Y: {physY} • {(int)(_selRect.Width * _dpiScaleX)} × {(int)(_selRect.Height * _dpiScaleY)} px");
        }
        else if (_currentDragMode == DragMode.Resizing)
        {
            double left = _origBox.Left;
            double top = _origBox.Top;
            double right = _origBox.Right;
            double bottom = _origBox.Bottom;

            switch (_activeHandle)
            {
                case HandleType.NW:
                    left = Math.Min(pt.X, right - minWidth);
                    top = Math.Min(pt.Y, bottom - minHeight);
                    break;
                case HandleType.N:
                    top = Math.Min(pt.Y, bottom - minHeight);
                    break;
                case HandleType.NE:
                    right = Math.Max(pt.X, left + minWidth);
                    top = Math.Min(pt.Y, bottom - minHeight);
                    break;
                case HandleType.E:
                    right = Math.Max(pt.X, left + minWidth);
                    break;
                case HandleType.SE:
                    right = Math.Max(pt.X, left + minWidth);
                    bottom = Math.Max(pt.Y, top + minHeight);
                    break;
                case HandleType.S:
                    bottom = Math.Max(pt.Y, top + minHeight);
                    break;
                case HandleType.SW:
                    left = Math.Min(pt.X, right - minWidth);
                    bottom = Math.Max(pt.Y, top + minHeight);
                    break;
                case HandleType.W:
                    left = Math.Min(pt.X, right - minWidth);
                    break;
            }

            left = Math.Max(0, left);
            top = Math.Max(0, top);
            right = Math.Min(ActualWidth, right);
            bottom = Math.Min(ActualHeight, bottom);

            _selRect = new Rect(left, top, Math.Max(minWidth, right - left), Math.Max(minHeight, bottom - top));
            UpdateSelectionVisuals(showHandles: true);
            ShowDimensionBadge($"{(int)(_selRect.Width * _dpiScaleX)} × {(int)(_selRect.Height * _dpiScaleY)} px");
        }
    }

    private void OnCanvasMouseUp(object sender, MouseButtonEventArgs e)
    {
        if (_currentDragMode != DragMode.None)
        {
            OverlayCanvas.ReleaseMouseCapture();
            _currentDragMode = DragMode.None;
            DimensionBadge.Visibility = Visibility.Collapsed;

            if (_selRect.Width > 15 && _selRect.Height > 15)
            {
                UpdateSelectionVisuals(showHandles: true);
                PositionHud();
            }
            else
            {
                _selRect = Rect.Empty;
                CutoutContainer.Visibility = Visibility.Collapsed;
                SelectionFrameCanvas.Visibility = Visibility.Collapsed;
                HandlesCanvas.Visibility = Visibility.Collapsed;
            }
        }
    }

    #endregion

    #region Visual Styling (Google Lens Viewfinder, 8 Handles, Cutout, HUD)

    private static StreamGeometry CreateCornerGeometry(System.Windows.Point p1, System.Windows.Point corner, System.Windows.Point p2)
    {
        var geom = new StreamGeometry();
        using (var ctx = geom.Open())
        {
            ctx.BeginFigure(p1, isFilled: false, isClosed: false);
            ctx.LineTo(corner, isStroked: true, isSmoothJoin: true);
            ctx.LineTo(p2, isStroked: true, isSmoothJoin: true);
        }
        geom.Freeze();
        return geom;
    }

    private void UpdateSelectionVisuals(bool showHandles)
    {
        if (_selRect.IsEmpty || _selRect.Width <= 1 || _selRect.Height <= 1)
        {
            CutoutContainer.Visibility = Visibility.Collapsed;
            SelectionFrameCanvas.Visibility = Visibility.Collapsed;
            HandlesCanvas.Visibility = Visibility.Collapsed;
            return;
        }

        double w = Math.Max(1, _selRect.Width);
        double h = Math.Max(1, _selRect.Height);

        // 1. Cutout Container (clips bright desktop to selection)
        CutoutContainer.Visibility = Visibility.Visible;
        Canvas.SetLeft(CutoutContainer, _selRect.X);
        Canvas.SetTop(CutoutContainer, _selRect.Y);
        CutoutContainer.Width = w;
        CutoutContainer.Height = h;
        BrightCutoutImage.Margin = new Thickness(-_selRect.X, -_selRect.Y, 0, 0);

        // 2. Selection Frame
        SelectionFrameCanvas.Visibility = Visibility.Visible;
        Canvas.SetLeft(SelectionShadowRect, _selRect.X - 1);
        Canvas.SetTop(SelectionShadowRect, _selRect.Y - 1);
        SelectionShadowRect.Width = w + 2;
        SelectionShadowRect.Height = h + 2;

        Canvas.SetLeft(SelectionRect, _selRect.X);
        Canvas.SetTop(SelectionRect, _selRect.Y);
        SelectionRect.Width = w;
        SelectionRect.Height = h;

        // 3. Google Lens Corner Brackets (3.5px round white)
        double c = Math.Min(22, Math.Min(w, h) / 4.0);
        if (c >= 2.0)
        {
            double x = _selRect.X;
            double y = _selRect.Y;
            double r = _selRect.Right;
            double b = _selRect.Bottom;

            CornerNw.Data = CreateCornerGeometry(new System.Windows.Point(x, y + c), new System.Windows.Point(x, y), new System.Windows.Point(x + c, y));
            CornerNe.Data = CreateCornerGeometry(new System.Windows.Point(r - c, y), new System.Windows.Point(r, y), new System.Windows.Point(r, y + c));
            CornerSe.Data = CreateCornerGeometry(new System.Windows.Point(r, b - c), new System.Windows.Point(r, b), new System.Windows.Point(r - c, b));
            CornerSw.Data = CreateCornerGeometry(new System.Windows.Point(x + c, b), new System.Windows.Point(x, b), new System.Windows.Point(x, b - c));
        }
        else
        {
            CornerNw.Data = null;
            CornerNe.Data = null;
            CornerSe.Data = null;
            CornerSw.Data = null;
        }

        // 4. 8 Circular Handles
        if (showHandles && w >= 15 && h >= 15)
        {
            HandlesCanvas.Visibility = Visibility.Visible;
            PositionHandle(HandleNw, _selRect.X, _selRect.Y);
            PositionHandle(HandleN, _selRect.X + w / 2.0, _selRect.Y);
            PositionHandle(HandleNe, _selRect.Right, _selRect.Y);
            PositionHandle(HandleE, _selRect.Right, _selRect.Y + h / 2.0);
            PositionHandle(HandleSe, _selRect.Right, _selRect.Bottom);
            PositionHandle(HandleS, _selRect.X + w / 2.0, _selRect.Bottom);
            PositionHandle(HandleSw, _selRect.X, _selRect.Bottom);
            PositionHandle(HandleW, _selRect.X, _selRect.Y + h / 2.0);
        }
        else
        {
            HandlesCanvas.Visibility = Visibility.Collapsed;
        }
    }

    private void PositionHandle(FrameworkElement handle, double cx, double cy)
    {
        Canvas.SetLeft(handle, cx - handle.Width / 2.0);
        Canvas.SetTop(handle, cy - handle.Height / 2.0);
    }

    private void ShowDimensionBadge(string text)
    {
        DimensionText.Text = text;
        DimensionBadge.Visibility = Visibility.Visible;
        PositionBadge();
    }

    private void PositionBadge()
    {
        double badgeX = _selRect.X + 8;
        double badgeY = _selRect.Y - 26;
        if (badgeY < 8) badgeY = _selRect.Y + 8;

        Canvas.SetLeft(DimensionBadge, Math.Max(8, badgeX));
        Canvas.SetTop(DimensionBadge, Math.Max(8, badgeY));
    }

    private void PositionHud()
    {
        HudControl.Measure(new System.Windows.Size(double.PositiveInfinity, double.PositiveInfinity));
        double hudW = HudControl.DesiredSize.Width > 50 ? HudControl.DesiredSize.Width : 350;
        double hudH = HudControl.DesiredSize.Height > 30 ? HudControl.DesiredSize.Height : 54;

        double hudX = _selRect.X + (_selRect.Width - hudW) / 2.0;
        hudX = Math.Max(12, Math.Min(ActualWidth - hudW - 12, hudX));

        double hudY = _selRect.Bottom + 12;
        // If bottom has no space, place above
        if (hudY + hudH + 12 > ActualHeight)
        {
            hudY = _selRect.Top - hudH - 12;
        }
        hudY = Math.Max(12, Math.Min(ActualHeight - hudH - 12, hudY));

        Canvas.SetLeft(HudControl, hudX);
        Canvas.SetTop(HudControl, hudY);
        HudControl.Visibility = Visibility.Visible;
    }

    private void PositionResultCard(FrameworkElement card)
    {
        double cardW = card.ActualWidth > 50 ? card.ActualWidth : 380;
        double cardH = card.ActualHeight > 50 ? card.ActualHeight : 160;

        double cardX = Canvas.GetLeft(HudControl);
        double cardY = Canvas.GetTop(HudControl) + HudControl.ActualHeight + 8;

        if (cardY + cardH + 12 > ActualHeight)
        {
            cardY = Math.Max(12, Canvas.GetTop(HudControl) - cardH - 8);
        }

        Canvas.SetLeft(card, Math.Max(12, Math.Min(ActualWidth - cardW - 12, cardX)));
        Canvas.SetTop(card, Math.Max(12, Math.Min(ActualHeight - cardH - 12, cardY)));
        card.Visibility = Visibility.Visible;
    }

    #endregion

    #region Action Execution (Search, Text/OCR, Translate, AI, Copy)

    private Bitmap? GetCurrentCroppedBitmap()
    {
        if (_selRect.IsEmpty || _selRect.Width <= 2 || _selRect.Height <= 2)
            return null;

        int physX = (int)(_physicalVirtualBounds.X + _selRect.X * _dpiScaleX);
        int physY = (int)(_physicalVirtualBounds.Y + _selRect.Y * _dpiScaleY);
        int physW = (int)(_selRect.Width * _dpiScaleX);
        int physH = (int)(_selRect.Height * _dpiScaleY);

        var area = new CaptureArea(physX, physY, physW, physH);
        return _capturer.Crop(area);
    }

    private async Task OnSearchActionAsync()
    {
        using var crop = GetCurrentCroppedBitmap();
        if (crop == null) return;

        _lensService.SearchWithImage(crop);
        await Task.Delay(100);
        CloseOverlay();
    }

    private async Task OnTextActionAsync()
    {
        using var crop = GetCurrentCroppedBitmap();
        if (crop == null) return;

        DimensionText.Text = "⏳ Распознавание текста...";
        DimensionBadge.Visibility = Visibility.Visible;
        PositionBadge();

        _cachedOcrResult ??= await Task.Run(() => _ocrService.RecognizeAsync(crop, _settings.DefaultLanguage));

        DimensionBadge.Visibility = Visibility.Collapsed;

        if (_cachedOcrResult.IsEmpty)
        {
            ShowDimensionBadge("Текст не обнаружен");
            return;
        }

        // Overlay text selection canvas exactly over cutout
        Canvas.SetLeft(OcrTextLayer, _selRect.X);
        Canvas.SetTop(OcrTextLayer, _selRect.Y);
        OcrTextLayer.Width = _selRect.Width;
        OcrTextLayer.Height = _selRect.Height;

        // Scale between OCR image pixels and WPF DIPs
        double scaleX = 1.0 / _dpiScaleX;
        double scaleY = 1.0 / _dpiScaleY;

        OcrTextLayer.SetOcrResult(_cachedOcrResult, scaleX, scaleY);
        OcrTextLayer.Visibility = Visibility.Visible;
        OcrTextLayer.Focus();
    }

    private async Task OnTranslateActionAsync()
    {
        using var crop = GetCurrentCroppedBitmap();
        if (crop == null) return;

        ResultCardTitle.Text = "🌐 Перевод текста";
        ResultCardContent.Text = "Выполняется OCR и перевод...";
        PositionResultCard(ResultCard);

        _cachedOcrResult ??= await Task.Run(() => _ocrService.RecognizeAsync(crop, _settings.DefaultLanguage));

        if (_cachedOcrResult.IsEmpty)
        {
            ResultCardContent.Text = "[На изображении нет текста для перевода]";
            return;
        }

        string translation = await Task.Run(() => _translateService.TranslateAsync(_cachedOcrResult.FullText, _settings.DefaultLanguage));
        ResultCardContent.Text = translation;
    }

    private async Task OnAiActionAsync()
    {
        using var crop = GetCurrentCroppedBitmap();
        if (crop == null) return;

        ResultCardTitle.Text = "🤖 Анализ AI";
        ResultCardContent.Text = "Запрос к AI...";
        PositionResultCard(ResultCard);

        string aiResponse = await Task.Run(() => _aiService.AnalyzeImageAsync(
            crop,
            "Опиши, что здесь изображено и переведи текст, если он есть.",
            _settings.GeminiApiKey,
            _settings.GeminiModel));

        ResultCardContent.Text = aiResponse;
    }

    private void OnCopyAction()
    {
        using var crop = GetCurrentCroppedBitmap();
        if (crop == null) return;

        _clipboardService.CopyImage(crop);
        ShowDimensionBadge("✓ Снимок скопирован в буфер!");
        Task.Delay(600).ContinueWith(_ => Dispatcher.Invoke(CloseOverlay));
    }

    private void CloseResultCard_Click(object sender, RoutedEventArgs e)
    {
        ResultCard.Visibility = Visibility.Collapsed;
    }

    private void BtnResultCopy_Click(object sender, RoutedEventArgs e)
    {
        string text = ResultCardContent.Text;
        if (!string.IsNullOrEmpty(text))
        {
            _clipboardService.CopyText(text);
            BtnResultCopy.Content = "✓ Скопировано!";
        }
    }

    #endregion

    protected override void OnClosed(EventArgs e)
    {
        _fullScreenshot?.Dispose();
        _fullScreenshot = null;
        base.OnClosed(e);
    }
}
