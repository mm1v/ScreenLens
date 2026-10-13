using System;
using System.Collections.Generic;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace ScreenLens.Next.Services;

public interface ITranslateService
{
    Task<string> TranslateAsync(string text, string targetLang = "ru", CancellationToken ct = default);
}

public class TranslateService : ITranslateService
{
    private static readonly HttpClient HttpClient = new();

    public async Task<string> TranslateAsync(string text, string targetLang = "ru", CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(text))
            return string.Empty;

        try
        {
            string url = $"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl={Uri.EscapeDataString(targetLang)}&dt=t";
            var formContent = new FormUrlEncodedContent(new[]
            {
                new KeyValuePair<string, string>("q", text)
            });

            using var response = await HttpClient.PostAsync(url, formContent, ct);
            response.EnsureSuccessStatusCode();

            string json = await response.Content.ReadAsStringAsync(ct);
            using var doc = JsonDocument.Parse(json);
            var root = doc.RootElement;

            if (root.ValueKind == JsonValueKind.Array && root.GetArrayLength() > 0)
            {
                var sentences = root[0];
                var sb = new StringBuilder();
                foreach (var item in sentences.EnumerateArray())
                {
                    if (item.ValueKind == JsonValueKind.Array && item.GetArrayLength() > 0)
                    {
                        string? part = item[0].GetString();
                        if (!string.IsNullOrEmpty(part))
                            sb.Append(part);
                    }
                }
                return sb.ToString().Trim();
            }

            return text;
        }
        catch (Exception ex)
        {
            return $"[Ошибка перевода: {ex.Message}]";
        }
    }
}
