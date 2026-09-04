using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows;

namespace LinovelibDesktop;

public partial class App : Application
{
    private const string SingleInstanceMutexName = @"Local\LinovelibEpubDownloader";
    private Mutex? _singleInstanceMutex;
    private bool _ownsSingleInstanceMutex;

    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    private static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    protected override void OnStartup(StartupEventArgs e)
    {
        _singleInstanceMutex = new Mutex(true, SingleInstanceMutexName, out var createdNew);
        _ownsSingleInstanceMutex = createdNew;
        if (!createdNew)
        {
            ActivateExistingWindow();
            Shutdown();
            return;
        }

        base.OnStartup(e);
    }

    protected override void OnExit(ExitEventArgs e)
    {
        if (_ownsSingleInstanceMutex) _singleInstanceMutex?.ReleaseMutex();
        _singleInstanceMutex?.Dispose();
        base.OnExit(e);
    }

    private static void ActivateExistingWindow()
    {
        var currentProcess = Process.GetCurrentProcess();
        var existing = Process.GetProcessesByName(currentProcess.ProcessName)
            .FirstOrDefault(process => process.Id != currentProcess.Id && process.MainWindowHandle != IntPtr.Zero);
        if (existing is null) return;

        ShowWindow(existing.MainWindowHandle, 9);
        SetForegroundWindow(existing.MainWindowHandle);
    }
}
