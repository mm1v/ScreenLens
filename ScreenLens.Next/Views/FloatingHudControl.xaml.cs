using System;
using System.Windows;
using System.Windows.Controls;

namespace ScreenLens.Next.Views;

public partial class FloatingHudControl : UserControl
{
    public event EventHandler? SearchClicked;
    public event EventHandler? TextClicked;
    public event EventHandler? TranslateClicked;
    public event EventHandler? AiClicked;
    public event EventHandler? CopyClicked;
    public event EventHandler? CloseClicked;

    public FloatingHudControl()
    {
        InitializeComponent();
    }

    private void BtnSearch_Click(object sender, RoutedEventArgs e) => SearchClicked?.Invoke(this, EventArgs.Empty);
    private void BtnText_Click(object sender, RoutedEventArgs e) => TextClicked?.Invoke(this, EventArgs.Empty);
    private void BtnTranslate_Click(object sender, RoutedEventArgs e) => TranslateClicked?.Invoke(this, EventArgs.Empty);
    private void BtnAi_Click(object sender, RoutedEventArgs e) => AiClicked?.Invoke(this, EventArgs.Empty);
    private void BtnCopy_Click(object sender, RoutedEventArgs e) => CopyClicked?.Invoke(this, EventArgs.Empty);
    private void BtnClose_Click(object sender, RoutedEventArgs e) => CloseClicked?.Invoke(this, EventArgs.Empty);
}
