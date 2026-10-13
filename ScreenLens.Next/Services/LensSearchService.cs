using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Text;

namespace ScreenLens.Next.Services;

public interface ILensSearchService
{
    bool SearchWithImage(Bitmap bitmap);
}

public class LensSearchService : ILensSearchService
{
    public bool SearchWithImage(Bitmap bitmap)
    {
        if (bitmap == null)
            return false;

        try
        {
            using var ms = new MemoryStream();
            bitmap.Save(ms, ImageFormat.Jpeg);
            string base64Image = Convert.ToBase64String(ms.ToArray());
            int w = bitmap.Width;
            int h = bitmap.Height;

            string html = $@"<!DOCTYPE html>
<html lang=""ru"">
<head>
    <meta charset=""utf-8"">
    <title>ScreenLens • Google Lens</title>
    <style>
        body {{
            background-color: #12151c;
            color: #00e5ff;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
            overflow: hidden;
        }}
        .spinner {{
            border: 4px solid rgba(0, 229, 255, 0.15);
            border-top: 4px solid #00e5ff;
            border-radius: 50%;
            width: 44px;
            height: 44px;
            animation: spin 0.8s linear infinite;
            margin-bottom: 16px;
        }}
        @keyframes spin {{ 0% {{ transform: rotate(0deg); }} 100% {{ transform: rotate(360deg); }} }}
        h2 {{ font-size: 17px; margin: 0 0 6px 0; color: #ffffff; font-weight: 600; }}
        p {{ font-size: 13px; color: #8892b0; margin: 0; }}
    </style>
</head>
<body>
    <div class=""spinner""></div>
    <h2>Поиск в Google Lens...</h2>
    <p>Браузер отправляет изображение в контексте вашей сессии</p>

    <form id=""lensForm"" action=""https://lens.google.com/v3/upload?hl=ru&ep=gisb&re=df&vpw=1920&vph=1080"" method=""POST"" enctype=""multipart/form-data"">
        <input type=""file"" id=""encoded_image"" name=""encoded_image"" style=""display:none;"">
        <input type=""hidden"" name=""processed_image_dimensions"" value=""{w},{h}"">
        <input type=""hidden"" name=""original_width"" value=""{w}"">
        <input type=""hidden"" name=""original_height"" value=""{h}"">
        <input type=""hidden"" name=""sbisrc"" value=""gisb"">
    </form>

    <script>
        function submitLens() {{
            try {{
                const b64 = ""{base64Image}"";
                const byteChars = atob(b64);
                const byteArray = new Uint8Array(byteChars.length);
                for (let i = 0; i < byteChars.length; i++) {{
                    byteArray[i] = byteChars.charCodeAt(i);
                }}
                const blob = new Blob([byteArray], {{ type: ""image/jpeg"" }});
                const file = new File([blob], ""screenshot.jpg"", {{ type: ""image/jpeg"" }});

                const dt = new DataTransfer();
                dt.items.add(file);
                document.getElementById(""encoded_image"").files = dt.files;
                document.getElementById(""lensForm"").submit();
            }} catch (err) {{
                console.error(""Auto-submit error:"", err);
                window.location.href = ""https://images.google.com"";
            }}
        }}

        if (document.readyState === ""loading"") {{
            document.addEventListener(""DOMContentLoaded"", submitLens);
        }} else {{
            submitLens();
        }}
    </script>
</body>
</html>";

            string tempHtmlPath = Path.Combine(Path.GetTempPath(), $"screenlens_lens_upload_{DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()}.html");
            File.WriteAllText(tempHtmlPath, html, Encoding.UTF8);

            var psi = new ProcessStartInfo
            {
                FileName = tempHtmlPath,
                UseShellExecute = true
            };
            Process.Start(psi);
            return true;
        }
        catch
        {
            return false;
        }
    }
}
