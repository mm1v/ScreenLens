using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace ScreenLens.Next.Services;

public interface IAiService
{
    Task<string> AnalyzeImageAsync(Bitmap bitmap, string prompt, string apiKey = "", string model = "gemini-1.5-flash", CancellationToken ct = default);
}

public class AiService : IAiService
{
    private static readonly HttpClient HttpClient = new();

    public async Task<string> AnalyzeImageAsync(Bitmap bitmap, string prompt, string apiKey = "", string model = "gemini-1.5-flash", CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(apiKey))
        {
            return "API ключ не настроен. Укажите Gemini API ключ для анализа изображений.";
        }

        try
        {
            using var ms = new MemoryStream();
            bitmap.Save(ms, ImageFormat.Jpeg);
            string base64Data = Convert.ToBase64String(ms.ToArray());

            string url = $"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={apiKey}";

            var requestBody = new
            {
                contents = new[]
                {
                    new
                    {
                        parts = new object[]
                        {
                            new { text = string.IsNullOrWhiteSpace(prompt) ? "Опиши, что изображено на картинке и распознай ключевую информацию." : prompt },
                            new
                            {
                                inline_data = new
                                {
                                    mime_type = "image/jpeg",
                                    data = base64Data
                                }
                            }
                        }
                    }
                }
            };

            string json = JsonSerializer.Serialize(requestBody);
            using var content = new StringContent(json, Encoding.UTF8, "application/json");

            using var response = await HttpClient.PostAsync(url, content, ct);
            string responseString = await response.Content.ReadAsStringAsync(ct);

            if (!response.IsSuccessStatusCode)
            {
                return $"Ошибка AI запроса ({response.StatusCode}): {responseString}";
            }

            using var doc = JsonDocument.Parse(responseString);
            var candidates = doc.RootElement.GetProperty("candidates");
            if (candidates.GetArrayLength() > 0)
            {
                var parts = candidates[0].GetProperty("content").GetProperty("parts");
                var sb = new StringBuilder();
                foreach (var p in parts.EnumerateArray())
                {
                    if (p.TryGetProperty("text", out var textElem))
                    {
                        sb.Append(textElem.GetString());
                    }
                }
                return sb.ToString().Trim();
            }

            return "Не удалось получить ответ от модели.";
        }
        catch (Exception ex)
        {
            return $"[Ошибка AI: {ex.Message}]";
        }
    }
}
