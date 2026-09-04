using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows;

namespace LinovelibSourceLauncher;

internal static class Program
{
    private const string UiProcessName = "LinovelibDesktop";

    [STAThread]
    private static int Main()
    {
        try
        {
            var root = FindProjectRoot();
            if (ActivateExistingUi()) return 0;

            BuildUi(root);
            StartUi(root);
            return 0;
        }
        catch (Exception error)
        {
            MessageBox.Show(
                error.Message,
                "轻小说 EPUB 下载器启动失败",
                MessageBoxButton.OK,
                MessageBoxImage.Error);
            return 1;
        }
    }

    private static string FindProjectRoot()
    {
        foreach (var start in new[] { AppContext.BaseDirectory, Environment.CurrentDirectory })
        {
            for (var directory = new DirectoryInfo(Path.GetFullPath(start)); directory is not null; directory = directory.Parent)
            {
                if (File.Exists(Path.Combine(directory.FullName, "main.py")) &&
                    File.Exists(Path.Combine(directory.FullName, "wpf_bridge.py")))
                    return directory.FullName;
            }
        }

        throw new DirectoryNotFoundException("找不到包含 main.py 与 wpf_bridge.py 的项目目录。");
    }

    private static bool ActivateExistingUi()
    {
        var existing = Process.GetProcessesByName(UiProcessName)
            .FirstOrDefault(process => !process.HasExited && process.MainWindowHandle != IntPtr.Zero);
        if (existing is null) return false;

        ShowWindow(existing.MainWindowHandle, 9);
        SetForegroundWindow(existing.MainWindowHandle);
        return true;
    }

    private static void BuildUi(string root)
    {
        var project = Path.Combine(root, "wpf", "LinovelibDesktop", "LinovelibDesktop.csproj");
        var startInfo = new ProcessStartInfo("dotnet")
        {
            WorkingDirectory = root,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
        };
        startInfo.ArgumentList.Add("build");
        startInfo.ArgumentList.Add(project);
        startInfo.ArgumentList.Add("--nologo");
        startInfo.ArgumentList.Add("--verbosity:minimal");

        using var build = Process.Start(startInfo)
            ?? throw new InvalidOperationException("无法启动 dotnet 构建工具。");
        var output = build.StandardOutput.ReadToEnd();
        var errors = build.StandardError.ReadToEnd();
        build.WaitForExit();
        if (build.ExitCode != 0)
        {
            var details = string.Join(Environment.NewLine, new[] { output, errors }
                .Where(text => !string.IsNullOrWhiteSpace(text)));
            throw new InvalidOperationException($"WPF 源码构建失败。{Environment.NewLine}{details}");
        }
    }

    private static void StartUi(string root)
    {
        var executable = Path.Combine(root, "wpf", "LinovelibDesktop", "bin", "Debug", "net8.0-windows", "LinovelibDesktop.exe");
        if (!File.Exists(executable))
            throw new FileNotFoundException("未找到刚构建的 WPF 界面程序。", executable);

        Process.Start(new ProcessStartInfo(executable)
        {
            WorkingDirectory = root,
            UseShellExecute = true,
        });
    }

    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr windowHandle);

    [DllImport("user32.dll")]
    private static extern bool ShowWindow(IntPtr windowHandle, int command);
}
