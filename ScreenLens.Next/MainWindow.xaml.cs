using System;
using System.Windows;
using System.Windows.Input;
using ScreenLens.Next.Capture;
using ScreenLens.Next.Core;
using ScreenLens.Next.OCR;
using ScreenLens.Next.Overlay;
using ScreenLens.Next.Services;

namespace ScreenLens.Next;

public partial class MainWindow : Window
{
    private readonly AppSettings _settings;
    private readonly ScreenCapturer _capturer;
    private readonly IOcrService _ocrService;
    private readonly ILensSearchService _lensService;
    private readonly ITranslateService _translateService;
    private readonly IAiService _aiService;
    private readonly IClipboardService _clipboardService;
    private readonly IGlobalHotkeyService _hotkeyService;

    private SelectionOverlayWindow? _activeOverlay;

    public MainWindow()
    {
        InitializeComponent();

        _settings = AppSettings.Load();
        _capturer = new ScreenCapturer();
        _ocrService = new WindowsMediaOcrService();
        _lensService = new LensSearchService();
        _translateService = new TranslateService();
        _aiService = new AiService();
        _clipboardService = new ClipboardService();
        _hotkeyService = new GlobalHotkeyService();

        Loaded += MainWindow_Loaded;
        Closed += MainWindow_Closed;
    }

    private void MainWindow_Loaded(object sender, RoutedEventArgs e)
    {
        Log("Инициализация ScreenLens Next (.NET 8 WPF)...");

        bool s1 = _hotkeyService.Register(ModifierKeys.Control | ModifierKeys.Alt, Key.S, TriggerCapture);
        bool s2 = _hotkeyService.Register(ModifierKeys.Control | ModifierKeys.Alt, Key.Space, TriggerCapture);

        if (s1 || s2)
        {
            Log($"[ХОТКЕИ] Зарегистрированы: Ctrl+Alt+S ({(s1 ? "OK" : "Занят")}), Ctrl+Alt+Space ({(s2 ? "OK" : "Занят")})");
        }
        else
        {
            Log("[ВНИМАНИЕ] Не удалось зарегистрировать хоткеи (возможно заняты другим приложением).");
            TxtStatus.Text = "Хоткеи не удалось привязать";
        }

        var bounds = DpiHelper.GetVirtualScreenPhysicalBounds();
        Log($"[ДИСПЛЕЙ] Виртуальный экран: {bounds.Width}x{bounds.Height} на ({bounds.X}, {bounds.Y})");
        Log("Готово к работе. Нажмите хоткей или кнопку 'Захватить сейчас'.");
    }

    private void MainWindow_Closed(object? sender, EventArgs e)
    {
        _hotkeyService.Dispose();
        _activeOverlay?.Close();
    }

    private void BtnCaptureNow_Click(object sender, RoutedEventArgs e)
    {
        TriggerCapture();
    }

    private void TriggerCapture()
    {
        Dispatcher.Invoke(() =>
        {
            if (_activeOverlay != null && _activeOverlay.IsLoaded)
            {
                _activeOverlay.Activate();
                return;
            }

            Log("[ЗАХВАТ] Активация режима оверлея...");
            try
            {
                _activeOverlay = new SelectionOverlayWindow(
                    _capturer,
                    _ocrService,
                    _lensService,
                    _translateService,
                    _aiService,
                    _clipboardService,
                    _settings
                );

                _activeOverlay.Closed += (s, e) =>
                {
                    _activeOverlay = null;
                    Log("[ЗАХВАТ] Оверлей закрыт.");
                };

                _activeOverlay.Show();
                _activeOverlay.Activate();
            }
            catch (Exception ex)
            {
                Log($"[ОШИБКА] Не удалось открыть оверлей: {ex.Message}");
            }
        });
    }

    private void Log(string message)
    {
        string timestamp = DateTime.Now.ToString("HH:mm:ss");
        TxtLog.AppendText($"[{timestamp}] {message}\n");
        TxtLog.ScrollToEnd();
    }
}