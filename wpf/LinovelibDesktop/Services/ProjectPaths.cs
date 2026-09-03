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

    public static string FindPython(string root)
    {
        var localPython = Path.Combine(root, ".venv", "Scripts", "python.exe");
        return File.Exists(localPython) ? localPython : "python";
    }
}
