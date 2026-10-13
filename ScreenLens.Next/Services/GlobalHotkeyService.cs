using System;
using System.Runtime.InteropServices;
using System.Windows.Input;
using System.Windows.Interop;

namespace ScreenLens.Next.Services;

public interface IGlobalHotkeyService : IDisposable
{
    bool Register(ModifierKeys modifiers, Key key, Action callback);
    void Unregister();
}

public class GlobalHotkeyService : IGlobalHotkeyService
{
    private const int WM_HOTKEY = 0x0312;
    private const uint MOD_ALT = 0x0001;
    private const uint MOD_CONTROL = 0x0002;
    private const uint MOD_SHIFT = 0x0004;
    private const uint MOD_WIN = 0x0008;
    private const uint MOD_NOREPEAT = 0x4000;

    [DllImport("user32.dll")]
    private static extern bool RegisterHotKey(IntPtr hWnd, int id, uint fsModifiers, uint vk);

    [DllImport("user32.dll")]
    private static extern bool UnregisterHotKey(IntPtr hWnd, int id);

    private int _idCounter = 9000;
    private readonly Dictionary<int, Action> _callbacks = new();
    private HwndSource? _hwndSource;

    private void EnsureHwndSource()
    {
        if (_hwndSource == null)
        {
            var parameters = new HwndSourceParameters("ScreenLensHotkeyListener")
            {
                WindowStyle = 0x800000 // WS_BORDER invisible
            };
            _hwndSource = new HwndSource(parameters);
            _hwndSource.AddHook(HwndHook);
        }
    }

    public bool Register(ModifierKeys modifiers, Key key, Action callback)
    {
        EnsureHwndSource();

        uint fsModifiers = MOD_NOREPEAT;
        if (modifiers.HasFlag(ModifierKeys.Alt)) fsModifiers |= MOD_ALT;
        if (modifiers.HasFlag(ModifierKeys.Control)) fsModifiers |= MOD_CONTROL;
        if (modifiers.HasFlag(ModifierKeys.Shift)) fsModifiers |= MOD_SHIFT;
        if (modifiers.HasFlag(ModifierKeys.Windows)) fsModifiers |= MOD_WIN;

        uint vk = (uint)KeyInterop.VirtualKeyFromKey(key);
        int id = ++_idCounter;

        bool success = RegisterHotKey(_hwndSource!.Handle, id, fsModifiers, vk);
        if (success)
        {
            _callbacks[id] = callback;
            return true;
        }
        return false;
    }

    private IntPtr HwndHook(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam, ref bool handled)
    {
        if (msg == WM_HOTKEY)
        {
            int id = wParam.ToInt32();
            if (_callbacks.TryGetValue(id, out var action))
            {
                action.Invoke();
                handled = true;
            }
        }
        return IntPtr.Zero;
    }

    public void Unregister()
    {
        if (_hwndSource != null)
        {
            foreach (var id in _callbacks.Keys)
            {
                UnregisterHotKey(_hwndSource.Handle, id);
            }
            _callbacks.Clear();
            _hwndSource.RemoveHook(HwndHook);
            _hwndSource.Dispose();
            _hwndSource = null;
        }
    }

    public void Dispose()
    {
        Unregister();
        GC.SuppressFinalize(this);
    }
}
