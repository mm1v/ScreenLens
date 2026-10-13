using System;
using System.IO;
using System.Text.Json;

namespace ScreenLens.Next.Core;

public class AppSettings
{
    public string Hotkey { get; set; } = "Ctrl+Alt+S";
    public string DefaultLanguage { get; set; } = "ru";
    public string GeminiApiKey { get; set; } = string.Empty;
    public string GeminiModel { get; set; } = "gemini-1.5-flash";
    public bool AutoCopyToClipboard { get; set; } = true;
    public bool SoundEnabled { get; set; } = true;

    private static readonly string SettingsPath = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
        "ScreenLens",
        "settings.json"
    );

    public static AppSettings Load()
    {
        try
        {
            if (File.Exists(SettingsPath))
            {
                string json = File.ReadAllText(SettingsPath);
                var settings = JsonSerializer.Deserialize<AppSettings>(json);
                if (settings != null) return settings;
            }
        }
        catch
        {
            // fallback to default
        }
        return new AppSettings();
    }

    public void Save()
    {
        try
        {
            string dir = Path.GetDirectoryName(SettingsPath)!;
            if (!Directory.Exists(dir))
            {
                Directory.CreateDirectory(dir);
            }
            string json = JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true });
            File.WriteAllText(SettingsPath, json);
        }
        catch
        {
            // ignore save errors
        }
    }
}
