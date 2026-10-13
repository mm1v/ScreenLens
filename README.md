# 🔍 ScreenLens.Next (.NET 8 / WPF Edition)

[![.NET 8.0](https://img.shields.io/badge/.NET-8.0-blue.svg)](https://dotnet.microsoft.com/)
[![WPF](https://img.shields.io/badge/GUI-WPF%20XAML-512BD4.svg)](https://learn.microsoft.com/dotnet/desktop/wpf/)
[![Platform Windows](https://img.shields.io/badge/Platform-Windows%2010%20%2F%2011-0078D6.svg)](https://www.microsoft.com/)
[![License MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-24%20passed-brightgreen.svg)](ScreenLens.Next.Tests/)

> **ScreenLens.Next** — высокопроизводительный нативный перенос ScreenLens на **C# / .NET 8 / WPF**, ориентированный на минимальное потребление ресурсов, ультра-плавный UX в стиле Google Lens, аппаратное ускорение рендеринга и мгновенную работу с Windows Media OCR.

---

## ✨ Архитектура и стек

- **Язык и среда**: C# 12, .NET 8 (`net8.0-windows10.0.19041.0`)
- **Графический интерфейс**: Windows Presentation Foundation (WPF), аппаратный рендеринг XAML
- **OCR**: Windows.Media.Ocr (нативное Windows Runtime API без внешних бинарников)
- **Захват экрана**: High-DPI Aware Win32 GDI / BitBlt захват экрана
- **Сниппинг и оверлей**: Аппаратно-ускоренный оверлей `SelectionOverlayWindow` с плавным затемнением и векторными ручками изменения размера (StreamGeometry)
- **Тесты**: xUnit тестовый комплект (`ScreenLens.Next.Tests`, 24 теста)

---

## 📂 Структура проекта

```text
ScreenLens.Next/
├── ScreenLens.Next.sln                 # .NET Solution файл
├── run_next.cmd                        # Быстрый запуск для Windows CMD
├── run_next.ps1                        # Быстрый запуск для PowerShell
├── assets/                             # Иконки и графические ресурсы
│
├── ScreenLens.Next/                    # Основной проект WPF
│   ├── App.xaml / App.xaml.cs          # Инициализация приложения и глобальный перехват ошибок
│   ├── MainWindow.xaml / .cs           # Главное окно и трей
│   │
│   ├── Capture/                        # Захват экрана и DPI
│   │   ├── DpiHelper.cs                # Преобразование координат с учётом системного масштабирования
│   │   └── ScreenCapturer.cs           # Полноэкранный захват дисплея
│   │
│   ├── Core/                           # Модели и настройки
│   │   ├── Models.cs                   # CaptureArea, TextWord, TextRegion, OcrResult
│   │   └── AppSettings.cs              # Конфигурация приложения
│   │
│   ├── OCR/                            # Распознавание текста
│   │   ├── IOcrService.cs              # Интерфейс OCR-сервиса
│   │   ├── TextLayoutHelper.cs         # Пространственная кластеризация строк и слов
│   │   └── WindowsMediaOcrService.cs   # Windows.Media.Ocr реализация
│   │
│   ├── Overlay/                        # Оверлей сниппинга
│   │   └── SelectionOverlayWindow.xaml # Окно интерактивного выделения
│   │
│   ├── Services/                       # Бизнес-сервисы
│   │   ├── GlobalHotkeyService.cs      # Win32 RegisterHotKey
│   │   ├── ClipboardService.cs         # Буфер обмена
│   │   ├── TranslateService.cs         # Сервис перевода
│   │   ├── AiService.cs                # Сервис Google Gemini Vision
│   │   └── LensSearchService.cs        # Поиск Google Lens
│   │
│   └── Views/                          # Пользовательские элементы управления
│       ├── FloatingHudControl.xaml     # Двухкапсульная плавающая панель
│       └── TextSelectionCanvas.xaml    # Интерактивное выделение распознанного текста
│
└── ScreenLens.Next.Tests/              # Набор модульных тестов (xUnit)
    ├── CaptureAreaTests.cs             # Тесты геометрии рамки
    ├── DpiHelperTests.cs               # Тесты масштабирования DPI
    └── TextLayoutHelperTests.cs        # Тесты кластеризации строк OCR
```

---

## 🚀 Сборка и запуск

### Требования
- Windows 10 (сборка 19041+) или Windows 11
- [.NET 8.0 SDK](https://dotnet.microsoft.com/download/dotnet/8.0)

### Запуск приложения
```powershell
# Запуск через PowerShell
./run_next.ps1

# Либо напрямую через dotnet CLI
dotnet run --project ScreenLens.Next
```

### Запуск тестов
```powershell
dotnet test ScreenLens.Next.sln
```

---

## 📄 Лицензия

Распространяется под свободной лицензией [MIT](LICENSE).
