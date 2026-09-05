using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

namespace MediaFlow.Installation
{
    // Independent overlay, never a PE resource or a multi-GB managed byte[].
    internal sealed class PrivateTemplatePayload
    {
        private const string Magic = "MF-TEMPLATE-001!";
        internal readonly Dictionary<string, object> Manifest;
        internal readonly long Offset;
        internal readonly long Length;
        internal readonly long ExpandedLength;
        internal string Hash { get { return (string)Manifest["sha256"]; } }
        private readonly string executable;

        private PrivateTemplatePayload(string path, Dictionary<string, object> manifest, long start)
        {
            executable = path; Manifest = manifest;
            Offset = Number(manifest, "offset"); Length = Number(manifest, "size_bytes");
            ExpandedLength = Number(manifest, "expanded_size_bytes");
            if (Offset < 2 || Length <= 0 || ExpandedLength < Length || Offset > start || Length != start - Offset)
                throw new InvalidDataException("私人模板载荷范围无效。");
            if (Number(manifest, "format_version") != 1 || (string)manifest["source"] != "private_snapshot"
                || (string)manifest["file_name"] != "template.mumudata" || !Object.Equals(manifest["private_data_possible"], true)
                || !Regex.IsMatch(Hash, "^[a-f0-9]{64}$")
                || !Regex.IsMatch((string)manifest["template_version"], "^[A-Za-z0-9._-]{1,80}$")
                || Number(manifest, "width") != 900 || Number(manifest, "height") != 1600 || Number(manifest, "dpi") != 320)
                throw new InvalidDataException("私人模板清单或显示标准不正确。");
        }

        private static long Number(Dictionary<string, object> value, string key)
        {
            object number = value[key];
            if (!(number is int) && !(number is long)) throw new InvalidDataException("模板长度格式无效。");
            return Convert.ToInt64(number);
        }

        internal static PrivateTemplatePayload Read(string path, bool required)
        {
            using (var source = File.OpenRead(path))
            {
                if (source.Length < 24) { if (required) throw new InvalidDataException("缺少私人模板载荷。"); return null; }
                source.Seek(-24, SeekOrigin.End);
                var reader = new BinaryReader(source);
                ulong count = reader.ReadUInt64();
                string magic = Encoding.ASCII.GetString(reader.ReadBytes(16));
                if (magic != Magic) { if (required) throw new InvalidDataException("私人模板尾部损坏。"); return null; }
                if (count == 0 || count > 65536 || (long)count > source.Length - 24)
                    throw new InvalidDataException("私人模板清单长度无效。");
                long start = source.Length - 24 - (long)count;
                source.Seek(start, SeekOrigin.Begin);
                var serializer = new JavaScriptSerializer { MaxJsonLength = 65536 };
                var manifest = serializer.Deserialize<Dictionary<string, object>>(Encoding.UTF8.GetString(reader.ReadBytes((int)count)));
                return new PrivateTemplatePayload(path, manifest, start);
            }
        }

        internal static string FileHash(string path)
        {
            using (var sha = SHA256.Create()) using (var stream = File.OpenRead(path))
                return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
        }

        internal void RequireSpace(string templateDirectory, string installDirectory, string mumuStorage)
        {
            // Aggregate allocations when paths share a drive; no assumed free space.
            var requirements = new Dictionary<string, long>(StringComparer.OrdinalIgnoreCase);
            AddSpace(requirements, templateDirectory, checked(Length + 512L * 1024 * 1024));
            AddSpace(requirements, installDirectory, 4L * 1024 * 1024 * 1024);
            AddSpace(requirements, mumuStorage, checked(ExpandedLength * 2 + 2L * 1024 * 1024 * 1024));
            AddSpace(requirements, Environment.GetFolderPath(Environment.SpecialFolder.Windows), 1024L * 1024 * 1024);
            foreach (var item in requirements)
                if (new DriveInfo(item.Key).AvailableFreeSpace < item.Value)
                    throw new IOException(item.Key + "空间不足，至少需要 " + (item.Value / 1024 / 1024) + " MB；尚未覆盖软件或导入模板。");
        }

        private static void AddSpace(Dictionary<string, long> requirements, string path, long bytes)
        {
            string root = Path.GetPathRoot(Path.GetFullPath(path));
            var drive = new DriveInfo(root);
            if (drive.DriveType != DriveType.Fixed) throw new IOException("模板及程序仅支持本地固定磁盘。");
            long previous; requirements.TryGetValue(root, out previous);
            requirements[root] = checked(previous + bytes);
        }

        internal string Extract(string directory, Action<int> progress, Func<bool> cancelled)
        {
            string output = Path.Combine(Path.GetFullPath(directory), Hash);
            Directory.CreateDirectory(output);
            string target = Path.Combine(output, "template.mumudata");
            string manifestPath = Path.Combine(output, "manifest.json");
            if (!File.Exists(target) || FileHash(target) != Hash)
            {
                string partial = target + ".partial";
                using (var source = File.OpenRead(executable)) using (var destination = File.Create(partial)) using (var sha = SHA256.Create())
                {
                    source.Seek(Offset, SeekOrigin.Begin);
                    var buffer = new byte[1024 * 1024];
                    long remaining = Length;
                    int previous = -1;
                    while (remaining > 0)
                    {
                        if (cancelled()) throw new OperationCanceledException("模板提取已取消；原模板未改动，残片保留，可重新安装。");
                        int read = source.Read(buffer, 0, (int)Math.Min(buffer.Length, remaining));
                        if (read == 0) throw new InvalidDataException("私人模板被截断。");
                        destination.Write(buffer, 0, read);
                        sha.TransformBlock(buffer, 0, read, buffer, 0);
                        remaining -= read;
                        int percent = (int)((Length - remaining) * 100.0 / Length);
                        if (percent != previous) { progress(percent); previous = percent; }
                    }
                    sha.TransformFinalBlock(new byte[0], 0, 0);
                    string hash = BitConverter.ToString(sha.Hash).Replace("-", "").ToLowerInvariant();
                    if (hash != Hash) throw new InvalidDataException("私人模板SHA-256不一致；不会激活，原模板不变。");
                }
                if (File.Exists(target))
                    throw new InvalidDataException("缓存存在不同内容的同名模板，已保留两份文件，请检查后重试。");
                File.Move(partial, target);
            }
            File.WriteAllText(manifestPath, new JavaScriptSerializer().Serialize(Manifest), new UTF8Encoding(false));
            return manifestPath;
        }
    }
}
