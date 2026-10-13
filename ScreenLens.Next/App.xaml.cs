using System;
using System.IO;
using System.Threading.Tasks;
using System.Windows;

namespace ScreenLens.Next;

/// <summary>
/// Interaction logic for App.xaml
/// </summary>
public partial class App : Application
{
    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);

        DispatcherUnhandledException += (sender, args) =>
        {
            LogCrash("DispatcherUnhandledException", args.Exception);
            args.Handled = true; // Prevent app termination
            MessageBox.Show($"Произошла ошибка: {args.Exception.Message}", "ScreenLens Next", MessageBoxButton.OK, MessageBoxImage.Warning);
        };

        AppDomain.CurrentDomain.UnhandledException += (sender, args) =>
        {
            if (args.ExceptionObject is Exception ex)
            {
                LogCrash("AppDomain.UnhandledException", ex);
            }
        };

        TaskScheduler.UnobservedTaskException += (sender, args) =>
        {
            LogCrash("TaskScheduler.UnobservedTaskException", args.Exception);
            args.SetObserved();
        };
    }

    private static void LogCrash(string source, Exception ex)
    {
        try
        {
            string logDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "ScreenLens");
            Directory.CreateDirectory(logDir);
            string logFile = Path.Combine(logDir, "crash.log");
            string entry = $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] [{source}] {ex}\n\n";
            File.AppendAllText(logFile, entry);
        }
        catch
        {
            // Ignore logging failures
        }
    }
}
