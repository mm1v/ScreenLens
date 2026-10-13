using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Windows;
using ScreenLens.Next.Core;

namespace ScreenLens.Next.OCR;

public static class TextLayoutHelper
{
    /// <summary>
    /// Groups individual words into lines based on vertical overlap.
    /// </summary>
    public static List<OcrLine> GroupWordsIntoLines(IEnumerable<OcrWord> words, double toleranceRatio = 0.5)
    {
        var wordList = words.Where(w => !string.IsNullOrWhiteSpace(w.Text)).ToList();
        if (wordList.Count == 0)
            return new List<OcrLine>();

        // Sort primarily by Y then X
        var sorted = wordList.OrderBy(w => w.Y).ThenBy(w => w.X).ToList();
        var lines = new List<List<OcrWord>>();

        foreach (var word in sorted)
        {
            double wordMidY = word.Y + (word.Height / 2.0);
            List<OcrWord>? matchedLine = null;

            foreach (var line in lines)
            {
                double lineAvgHeight = line.Average(w => w.Height);
                double lineAvgMidY = line.Average(w => w.Y + (w.Height / 2.0));
                double maxDiff = lineAvgHeight * toleranceRatio;

                if (Math.Abs(wordMidY - lineAvgMidY) <= maxDiff)
                {
                    matchedLine = line;
                    break;
                }
            }

            if (matchedLine != null)
            {
                matchedLine.Add(word);
            }
            else
            {
                lines.Add(new List<OcrWord> { word });
            }
        }

        var result = new List<OcrLine>();
        foreach (var rawLine in lines.OrderBy(l => l.Average(w => w.Y)))
        {
            var orderedWords = rawLine.OrderBy(w => w.X).ToList();
            double minX = orderedWords.Min(w => w.X);
            double minY = orderedWords.Min(w => w.Y);
            double maxX = orderedWords.Max(w => w.Right);
            double maxY = orderedWords.Max(w => w.Bottom);
            var rect = new Rect(minX, minY, Math.Max(1, maxX - minX), Math.Max(1, maxY - minY));

            string lineText = string.Join(" ", orderedWords.Select(w => w.Text));
            result.Add(new OcrLine(lineText, rect, orderedWords));
        }

        return result;
    }

    /// <summary>
    /// Splits a sequence of words on a single horizontal line into cohesive block segments
    /// if the gap between consecutive words exceeds the threshold.
    /// </summary>
    public static List<List<OcrWord>> SplitLineByHorizontalGaps(IReadOnlyList<OcrWord> lineWords, double gapThreshold = 40.0)
    {
        var segments = new List<List<OcrWord>>();
        if (lineWords == null || lineWords.Count == 0)
            return segments;

        var currentSegment = new List<OcrWord> { lineWords[0] };

        for (int i = 1; i < lineWords.Count; i++)
        {
            var prev = lineWords[i - 1];
            var cur = lineWords[i];
            double gap = cur.X - prev.Right;

            // Also check dynamic gap based on character width
            double dynamicGap = Math.Max(gapThreshold, prev.Height * 1.8);

            if (gap > dynamicGap)
            {
                segments.Add(currentSegment);
                currentSegment = new List<OcrWord> { cur };
            }
            else
            {
                currentSegment.Add(cur);
            }
        }

        if (currentSegment.Count > 0)
            segments.Add(currentSegment);

        return segments;
    }

    /// <summary>
    /// Formats selected words into clipboard text with proper spacing and line breaks.
    /// </summary>
    public static string FormatSelectedWordsToText(IEnumerable<OcrWord> selectedWords)
    {
        var words = selectedWords?.Where(w => !string.IsNullOrWhiteSpace(w.Text)).ToList();
        if (words == null || words.Count == 0)
            return string.Empty;

        var lines = GroupWordsIntoLines(words);
        var sb = new StringBuilder();

        for (int i = 0; i < lines.Count; i++)
        {
            var line = lines[i];
            var segments = SplitLineByHorizontalGaps(line.Words);

            for (int s = 0; s < segments.Count; s++)
            {
                string segText = string.Join(" ", segments[s].Select(w => w.Text));
                sb.Append(segText);
                if (s < segments.Count - 1)
                    sb.Append("    "); // Horizontal column separator
            }

            if (i < lines.Count - 1)
                sb.AppendLine();
        }

        return sb.ToString().Trim();
    }

    /// <summary>
    /// Determines whether a word intersects a selection rectangle or touch range.
    /// </summary>
    public static bool IntersectsSelection(OcrWord word, Rect selectionBox)
    {
        return word.BoundingRect.IntersectsWith(selectionBox);
    }
}
