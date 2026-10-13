using System;
using System.Collections.Generic;
using System.Windows;
using ScreenLens.Next.Core;
using ScreenLens.Next.OCR;
using Xunit;

namespace ScreenLens.Next.Tests;

public class TextLayoutHelperTests
{
    [Fact]
    public void GroupWordsIntoLines_GroupsWordsOnSameRow()
    {
        // Two words on row 1 (Y ~ 100), two words on row 2 (Y ~ 140)
        var words = new List<OcrWord>
        {
            new("Привет", new Rect(20, 100, 60, 20)),
            new("Мир", new Rect(90, 102, 40, 20)),
            new("Вторая", new Rect(20, 140, 70, 20)),
            new("Строка", new Rect(100, 141, 65, 20)),
        };

        var lines = TextLayoutHelper.GroupWordsIntoLines(words);

        Assert.Equal(2, lines.Count);
        Assert.Equal("Привет Мир", lines[0].Text);
        Assert.Equal("Вторая Строка", lines[1].Text);
    }

    [Fact]
    public void GroupWordsIntoLines_SortsByReadingOrder()
    {
        // Scrambled input order
        var words = new List<OcrWord>
        {
            new("Внизу-Справа", new Rect(200, 300, 100, 20)),
            new("Вверху-Слева", new Rect(10, 50, 100, 20)),
            new("Вверху-Справа", new Rect(120, 50, 100, 20)),
            new("Внизу-Слева", new Rect(10, 300, 100, 20)),
        };

        var lines = TextLayoutHelper.GroupWordsIntoLines(words);

        Assert.Equal(2, lines.Count);
        Assert.Equal("Вверху-Слева Вверху-Справа", lines[0].Text);
        Assert.Equal("Внизу-Слева Внизу-Справа", lines[1].Text);
    }

    [Fact]
    public void SplitLineByHorizontalGaps_SplitsColumnsAcrossLargeGap()
    {
        // Table row with Column 1 and Column 2 separated by 120px gap
        var lineWords = new List<OcrWord>
        {
            new("Item1", new Rect(10, 50, 50, 15)),
            new("Value1", new Rect(65, 50, 50, 15)),
            // Large horizontal gap between X=115 and X=250 (gap = 135px)
            new("Item2", new Rect(250, 50, 50, 15)),
            new("Value2", new Rect(305, 50, 50, 15)),
        };

        var segments = TextLayoutHelper.SplitLineByHorizontalGaps(lineWords, gapThreshold: 40.0);

        Assert.Equal(2, segments.Count);
        Assert.Equal(2, segments[0].Count);
        Assert.Equal("Item1", segments[0][0].Text);
        Assert.Equal("Value1", segments[0][1].Text);

        Assert.Equal(2, segments[1].Count);
        Assert.Equal("Item2", segments[1][0].Text);
        Assert.Equal("Value2", segments[1][1].Text);
    }

    [Fact]
    public void FormatSelectedWordsToText_FormatsWithLineBreaksAndColumns()
    {
        var words = new List<OcrWord>
        {
            // Row 1: two columns
            new("ColA1", new Rect(10, 50, 40, 15)),
            new("ColB1", new Rect(200, 50, 40, 15)),
            // Row 2: two columns
            new("ColA2", new Rect(10, 80, 40, 15)),
            new("ColB2", new Rect(200, 80, 40, 15)),
        };

        string formatted = TextLayoutHelper.FormatSelectedWordsToText(words);
        string[] lines = formatted.Split(new[] { "\r\n", "\n" }, StringSplitOptions.None);

        Assert.Equal(2, lines.Length);
        Assert.Contains("    ", lines[0]); // Contains column separation
        Assert.StartsWith("ColA1", lines[0]);
        Assert.EndsWith("ColB1", lines[0]);

        Assert.StartsWith("ColA2", lines[1]);
        Assert.EndsWith("ColB2", lines[1]);
    }

    [Fact]
    public void FormatSelectedWordsToText_EmptyInput_ReturnsEmptyString()
    {
        var result = TextLayoutHelper.FormatSelectedWordsToText(Array.Empty<OcrWord>());
        Assert.Equal(string.Empty, result);
    }

    [Fact]
    public void IntersectsSelection_DetectsOverlapCorrectly()
    {
        var word = new OcrWord("Тест", new Rect(50, 50, 100, 30));

        var overlappingBox = new Rect(40, 40, 30, 30);
        var disjointBox = new Rect(200, 200, 50, 50);

        Assert.True(TextLayoutHelper.IntersectsSelection(word, overlappingBox));
        Assert.False(TextLayoutHelper.IntersectsSelection(word, disjointBox));
    }
}
