using System.Windows;
using ScreenLens.Next.Capture;
using Xunit;

namespace ScreenLens.Next.Tests;

public class DpiHelperTests
{
    [Theory]
    [InlineData(100, 200, 300, 400, 1.0, 1.0, 100, 200, 300, 400)]
    [InlineData(125, 250, 375, 500, 1.25, 1.25, 100, 200, 300, 400)]
    [InlineData(150, 300, 450, 600, 1.5, 1.5, 100, 200, 300, 400)]
    [InlineData(200, 400, 600, 800, 2.0, 2.0, 100, 200, 300, 400)]
    public void PhysicalToDip_CalculatesCorrectDIPs(
        double px, double py, double pw, double ph,
        double scaleX, double scaleY,
        double expX, double expY, double expW, double expH)
    {
        var phys = new Rect(px, py, pw, ph);
        var dip = DpiHelper.PhysicalToDip(phys, scaleX, scaleY);

        Assert.Equal(expX, dip.X, 2);
        Assert.Equal(expY, dip.Y, 2);
        Assert.Equal(expW, dip.Width, 2);
        Assert.Equal(expH, dip.Height, 2);
    }

    [Theory]
    [InlineData(100, 200, 300, 400, 1.0, 1.0, 100, 200, 300, 400)]
    [InlineData(100, 200, 300, 400, 1.25, 1.25, 125, 250, 375, 500)]
    [InlineData(100, 200, 300, 400, 1.5, 1.5, 150, 300, 450, 600)]
    [InlineData(100, 200, 300, 400, 2.0, 2.0, 200, 400, 600, 800)]
    public void DipToPhysical_CalculatesCorrectPhysicalPixels(
        double dx, double dy, double dw, double dh,
        double scaleX, double scaleY,
        double expX, double expY, double expW, double expH)
    {
        var dip = new Rect(dx, dy, dw, dh);
        var phys = DpiHelper.DipToPhysical(dip, scaleX, scaleY);

        Assert.Equal(expX, phys.X, 2);
        Assert.Equal(expY, phys.Y, 2);
        Assert.Equal(expW, phys.Width, 2);
        Assert.Equal(expH, phys.Height, 2);
    }

    [Fact]
    public void MultiMonitor_NegativeCoordinatesHandledCorrectly()
    {
        // Monitor positioned to the left of primary display: X = -1920
        var phys = new Rect(-1920, 0, 1920, 1080);
        var dip = DpiHelper.PhysicalToDip(phys, 1.25, 1.25);

        Assert.Equal(-1536, dip.X, 2);
        Assert.Equal(0, dip.Y, 2);
        Assert.Equal(1536, dip.Width, 2);
        Assert.Equal(864, dip.Height, 2);

        var roundtrip = DpiHelper.DipToPhysical(dip, 1.25, 1.25);
        Assert.Equal(phys.X, roundtrip.X, 2);
        Assert.Equal(phys.Y, roundtrip.Y, 2);
        Assert.Equal(phys.Width, roundtrip.Width, 2);
        Assert.Equal(phys.Height, roundtrip.Height, 2);
    }

    [Fact]
    public void PointTransformations_RoundTripAccurately()
    {
        var point = new Point(543.21, 987.65);
        var dip = DpiHelper.PhysicalToDip(point, 1.5, 1.5);
        var back = DpiHelper.DipToPhysical(dip, 1.5, 1.5);

        Assert.Equal(point.X, back.X, 3);
        Assert.Equal(point.Y, back.Y, 3);
    }
}
