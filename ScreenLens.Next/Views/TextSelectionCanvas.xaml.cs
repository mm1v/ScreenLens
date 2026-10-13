using System;
using System.Collections.Generic;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Shapes;
using ScreenLens.Next.Core;
using ScreenLens.Next.OCR;
using ScreenLens.Next.Services;

namespace ScreenLens.Next.Views;

public partial class TextSelectionCanvas : UserControl
{
    private OcrResult? _ocrResult;
    private readonly HashSet<OcrWord> _selectedWords = new();
    private readonly IClipboardService _clipboardService = new ClipboardService();

    private bool _isDragging;
    private Point _dragStartPoint;
    private double _scaleX = 1.0;
    private double _scaleY = 1.0;

    public event EventHandler<string>? TextCopied;

    public TextSelectionCanvas()
    {
        InitializeComponent();
        SelectionActionPill.MouseLeftButtonUp += (s, e) => CopySelectedText();
        KeyDown += OnKeyDown;
    }

    public void SetOcrResult(OcrResult ocrResult, double scaleX = 1.0, double scaleY = 1.0)
    {
        _ocrResult = ocrResult;
        _scaleX = scaleX > 0 ? scaleX : 1.0;
        _scaleY = scaleY > 0 ? scaleY : 1.0;
        ClearSelection();
    }

    public void ClearSelection()
    {
        _selectedWords.Clear();
        WordHighlightsCanvas.Children.Clear();
        SelectionActionPill.Visibility = Visibility.Collapsed;
        RubberBandRect.Visibility = Visibility.Collapsed;
    }

    protected override void OnMouseLeftButtonDown(MouseButtonEventArgs e)
    {
        base.OnMouseLeftButtonDown(e);
        Focus();

        _dragStartPoint = e.GetPosition(this);
        _isDragging = true;
        CaptureMouse();

        if (e.ClickCount == 2)
        {
            // Double click: select single word under cursor
            SelectWordAtPoint(_dragStartPoint);
            _isDragging = false;
            ReleaseMouseCapture();
            return;
        }

        ClearSelection();
        RubberBandRect.Visibility = Visibility.Visible;
        Canvas.SetLeft(RubberBandRect, _dragStartPoint.X);
        Canvas.SetTop(RubberBandRect, _dragStartPoint.Y);
        RubberBandRect.Width = 0;
        RubberBandRect.Height = 0;
    }

    protected override void OnMouseMove(MouseEventArgs e)
    {
        base.OnMouseMove(e);
        if (!_isDragging || _ocrResult == null) return;

        Point currentPoint = e.GetPosition(this);
        double x = Math.Min(_dragStartPoint.X, currentPoint.X);
        double y = Math.Min(_dragStartPoint.Y, currentPoint.Y);
        double width = Math.Abs(currentPoint.X - _dragStartPoint.X);
        double height = Math.Abs(currentPoint.Y - _dragStartPoint.Y);

        Canvas.SetLeft(RubberBandRect, x);
        Canvas.SetTop(RubberBandRect, y);
        RubberBandRect.Width = width;
        RubberBandRect.Height = height;

        var selectionRect = new Rect(x, y, width, height);
        UpdateSelectionFromRect(selectionRect);
    }

    protected override void OnMouseLeftButtonUp(MouseButtonEventArgs e)
    {
        base.OnMouseLeftButtonUp(e);
        if (_isDragging)
        {
            _isDragging = false;
            ReleaseMouseCapture();
            RubberBandRect.Visibility = Visibility.Collapsed;
            UpdateActionPillPosition();
        }
    }

    private void SelectWordAtPoint(Point pt)
    {
        if (_ocrResult == null) return;

        // Convert UI coordinate to image coordinate
        double imgX = pt.X / _scaleX;
        double imgY = pt.Y / _scaleY;

        var targetWord = _ocrResult.AllWords.FirstOrDefault(w => w.BoundingRect.Contains(new Point(imgX, imgY)));
        if (targetWord != null)
        {
            _selectedWords.Clear();
            _selectedWords.Add(targetWord);
            RenderWordHighlights();
            UpdateActionPillPosition();
        }
    }

    private void UpdateSelectionFromRect(Rect uiSelectionRect)
    {
        if (_ocrResult == null) return;

        // Convert UI selection rect to Image coordinate space
        var imgRect = new Rect(
            uiSelectionRect.X / _scaleX,
            uiSelectionRect.Y / _scaleY,
            uiSelectionRect.Width / _scaleX,
            uiSelectionRect.Height / _scaleY
        );

        _selectedWords.Clear();
        foreach (var word in _ocrResult.AllWords)
        {
            if (TextLayoutHelper.IntersectsSelection(word, imgRect))
            {
                _selectedWords.Add(word);
            }
        }

        RenderWordHighlights();
    }

    private void RenderWordHighlights()
    {
        WordHighlightsCanvas.Children.Clear();
        if (_selectedWords.Count == 0) return;

        // Group into lines for cohesive aesthetic block highlights
        var lines = TextLayoutHelper.GroupWordsIntoLines(_selectedWords);

        foreach (var line in lines)
        {
            var segments = TextLayoutHelper.SplitLineByHorizontalGaps(line.Words);

            foreach (var seg in segments)
            {
                double minX = seg.Min(w => w.X) * _scaleX;
                double minY = seg.Min(w => w.Y) * _scaleY;
                double maxX = seg.Max(w => w.Right) * _scaleX;
                double maxY = seg.Max(w => w.Bottom) * _scaleY;

                var highlightRect = new Border
                {
                    Background = new SolidColorBrush(Color.FromArgb(0x40, 0x29, 0x79, 0xFF)),
                    BorderBrush = new SolidColorBrush(Color.FromArgb(0xC0, 0x29, 0x79, 0xFF)),
                    BorderThickness = new Thickness(1),
                    CornerRadius = new CornerRadius(3),
                    Width = Math.Max(2, maxX - minX),
                    Height = Math.Max(2, maxY - minY)
                };

                Canvas.SetLeft(highlightRect, minX);
                Canvas.SetTop(highlightRect, minY);
                WordHighlightsCanvas.Children.Add(highlightRect);
            }
        }
    }

    private void UpdateActionPillPosition()
    {
        if (_selectedWords.Count == 0)
        {
            SelectionActionPill.Visibility = Visibility.Collapsed;
            return;
        }

        double minX = _selectedWords.Min(w => w.X) * _scaleX;
        double minY = _selectedWords.Min(w => w.Y) * _scaleY;
        double maxX = _selectedWords.Max(w => w.Right) * _scaleX;
        double maxY = _selectedWords.Max(w => w.Bottom) * _scaleY;

        double pillWidth = 110;
        double pillHeight = 26;

        double pillX = (minX + maxX) / 2.0 - (pillWidth / 2.0);
        pillX = Math.Max(5, Math.Min(ActualWidth - pillWidth - 5, pillX));

        double pillY = minY - pillHeight - 6;
        if (pillY < 5)
        {
            pillY = maxY + 6;
        }

        Canvas.SetLeft(SelectionActionPill, pillX);
        Canvas.SetTop(SelectionActionPill, pillY);
        SelectionActionPill.Visibility = Visibility.Visible;
    }

    public void CopySelectedText()
    {
        if (_selectedWords.Count == 0 && _ocrResult != null)
        {
            // If nothing selected, copy all text
            string allText = _ocrResult.FullText;
            if (!string.IsNullOrEmpty(allText))
            {
                _clipboardService.CopyText(allText);
                PillText.Text = "✓ Скопировано!";
                TextCopied?.Invoke(this, allText);
            }
            return;
        }

        string formattedText = TextLayoutHelper.FormatSelectedWordsToText(_selectedWords);
        if (!string.IsNullOrEmpty(formattedText))
        {
            _clipboardService.CopyText(formattedText);
            PillText.Text = "✓ Скопировано!";
            TextCopied?.Invoke(this, formattedText);
        }
    }

    private void OnKeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.C && (Keyboard.Modifiers & ModifierKeys.Control) == ModifierKeys.Control)
        {
            CopySelectedText();
            e.Handled = true;
        }
        else if (e.Key == Key.A && (Keyboard.Modifiers & ModifierKeys.Control) == ModifierKeys.Control)
        {
            // Select all
            if (_ocrResult != null && _ocrResult.AllWords.Count > 0)
            {
                _selectedWords.Clear();
                foreach (var w in _ocrResult.AllWords) _selectedWords.Add(w);
                RenderWordHighlights();
                UpdateActionPillPosition();
                e.Handled = true;
            }
        }
    }
}
