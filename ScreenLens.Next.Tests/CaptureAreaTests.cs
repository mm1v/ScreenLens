using ScreenLens.Next.Core;
using Xunit;

namespace ScreenLens.Next.Tests;

public class CaptureAreaTests
{
    [Fact]
    public void FromPoints_NormalizesInvertedCoordinates()
    {
        // Dragging from bottom-right (400, 300) to top-left (100, 50)
        var area = CaptureArea.FromPoints(400, 300, 100, 50);

        Assert.Equal(100, area.X);
        Assert.Equal(50, area.Y);
        Assert.Equal(300, area.Width);
        Assert.Equal(250, area.Height);
        Assert.Equal(400, area.Right);
        Assert.Equal(300, area.Bottom);
        Assert.True(area.IsValid);
    }

    [Theory]
    [InlineData(0, 0, 1, 1, false)]
    [InlineData(10, 10, 2, 2, false)]
    [InlineData(10, 10, 3, 3, true)]
    [InlineData(50, 50, 200, 150, true)]
    public void IsValid_RejectsTooSmallSelections(int x, int y, int w, int h, bool expectedValid)
    {
        var area = new CaptureArea(x, y, w, h);
        Assert.Equal(expectedValid, area.IsValid);
    }

    [Fact]
    public void OcrResult_Empty_ReturnsCorrectDefaults()
    {
        var empty = OcrResult.Empty(800, 600);

        Assert.True(empty.IsEmpty);
        Assert.Equal(string.Empty, empty.FullText);
        Assert.Empty(empty.Lines);
        Assert.Empty(empty.AllWords);
        Assert.Equal(800, empty.ImageWidth);
        Assert.Equal(600, empty.ImageHeight);
    }
}
