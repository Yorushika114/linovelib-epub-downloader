using System.IO;

namespace LinovelibDesktop.Services;

public static class ProjectPaths
{
    public static string FindRoot()
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

    /// <summary>
    /// 解析要使用的 Python 解释器，按「内置运行时 → 项目 venv → 系统 python」回退。
    ///
    /// 分发版把嵌入式 Python 放在 runtime/python/（见
    /// docs/superpowers/specs/2026-09-10-portable-runtime-design.md），
    /// 故此处优先探测它；源码版开发机没有该目录，自然回落到 .venv 或系统
    /// python。三个入口共用同一条解析链，避免分发版与源码版行为分叉。
    /// </summary>
    public static string FindPython(string root)
    {
        var bundled = Path.Combine(root, "runtime", "python", "python.exe");
        if (File.Exists(bundled)) return bundled;

        var localPython = Path.Combine(root, ".venv", "Scripts", "python.exe");
        return File.Exists(localPython) ? localPython : "python";
    }
}
