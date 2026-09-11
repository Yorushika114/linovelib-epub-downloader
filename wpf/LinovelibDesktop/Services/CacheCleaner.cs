using System.IO;

namespace LinovelibDesktop.Services;

/// <summary>
/// 退出时清理缓存目录（<see cref="ProjectPaths.CacheDirectoryName"/>）里**本程序自己产出**的内容。
///
/// 只动下面三类可再生的中间产物——章节插图暂存、卷页 HTML、EPUB 临时文件——删掉不影响
/// download/ 下的成品，下次重新抓取即可。目录里其它文件（开发期手工放进去的侦察脚本、
/// 运行日志等）一律不碰：该目录整个在 .gitignore 里，删掉不可恢复。
///
/// 这份清单必须与 Python 侧 linovelib/paths.py 的同名常量一致，由
/// tests/test_cache_cleanup.py 跨语言断言守着。
///
/// 整个清理是 best-effort：被占用/无权限的条目一律跳过留给下次，绝不抛异常，也绝不
/// 因为清理失败挡住窗口关闭。
/// </summary>
public static class CacheCleaner
{
    private const string ImageCacheDirName = "images";
    private const string VolumePagePattern = "vol_*_page.html";
    private const string TempEpubPattern = "*.epub.tmp";

    /// <summary>清理缓存，返回清理前的 (文件数, 字节数)。目录本就不存在时返回 (0, 0)。</summary>
    public static (long Files, long Bytes) Clean(string root)
    {
        string dir;
        try
        {
            dir = ProjectPaths.FindCacheDir(root);
            if (!Directory.Exists(dir)) return (0, 0);
        }
        catch (Exception)
        {
            return (0, 0);
        }

        // 先量再删：删的过程中目录在变小，量出来就没有意义了。
        var usage = Measure(dir);
        Clear(dir);
        return usage;
    }

    private static void Clear(string dir)
    {
        foreach (var pattern in new[] { VolumePagePattern, TempEpubPattern })
        {
            foreach (var path in SafeGetFiles(dir, pattern, SearchOption.TopDirectoryOnly))
            {
                try { File.Delete(path); }
                catch (Exception)
                {
                    // 被占用：留着，下次退出再清。
                }
            }
        }

        var images = Path.Combine(dir, ImageCacheDirName);
        if (Directory.Exists(images)) TryDeleteTree(images);

        // 缓存清空后目录本身也顺手收掉；里面还留着非缓存文件时非递归删除会失败，正好保留。
        try { Directory.Delete(dir, recursive: false); }
        catch (Exception) { }
    }

    private static (long Files, long Bytes) Measure(string dir)
    {
        long files = 0;
        long bytes = 0;

        foreach (var pattern in new[] { VolumePagePattern, TempEpubPattern })
        {
            foreach (var path in SafeGetFiles(dir, pattern, SearchOption.TopDirectoryOnly))
            {
                Count(path, ref files, ref bytes);
            }
        }

        var images = Path.Combine(dir, ImageCacheDirName);
        if (Directory.Exists(images))
        {
            foreach (var path in SafeGetFiles(images, "*", SearchOption.AllDirectories))
            {
                Count(path, ref files, ref bytes);
            }
        }

        return (files, bytes);
    }

    private static void Count(string path, ref long files, ref long bytes)
    {
        try
        {
            files++;
            bytes += new FileInfo(path).Length;
        }
        catch (Exception)
        {
            // 统计期间文件被删掉：计数不完整不影响清理本身。
        }
    }

    /// <summary>枚举失败（目录不存在、无权访问、枚举中途被删）时返回空，不往外抛。</summary>
    private static string[] SafeGetFiles(string dir, string pattern, SearchOption option)
    {
        try
        {
            return Directory.GetFiles(dir, pattern, option);
        }
        catch (Exception)
        {
            return Array.Empty<string>();
        }
    }

    private static void TryDeleteTree(string dir)
    {
        try
        {
            Directory.Delete(dir, recursive: true);
            return;
        }
        catch (Exception)
        {
            // 多半是杀软/其它进程短暂占用，或子树里有只读属性。清掉属性再试一次。
        }

        foreach (var path in SafeGetFiles(dir, "*", SearchOption.AllDirectories))
        {
            try { File.SetAttributes(path, FileAttributes.Normal); }
            catch (Exception) { }
        }

        try { Directory.Delete(dir, recursive: true); }
        catch (Exception)
        {
            // 仍失败：留着，下次启动/退出时再清。
        }
    }
}
