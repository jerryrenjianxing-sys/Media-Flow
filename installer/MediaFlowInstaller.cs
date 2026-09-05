using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;

[assembly: AssemblyTitle("MediaFlow Installer")]
[assembly: AssemblyDescription("MediaFlow 媒体自动化平台安装向导")]
[assembly: AssemblyCompany("MediaFlow")]
[assembly: AssemblyProduct("MediaFlow")]
[assembly: AssemblyVersion("0.4.1.0")]
[assembly: AssemblyFileVersion("0.4.1.0")]

namespace MediaFlow.Installation
{
    internal static class Program
    {
        [STAThread]
        private static int Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            using (var window = new InstallerWindow(args))
            {
                if (Array.IndexOf(args, "--quiet") >= 0) return window.RunQuiet();
                Application.Run(window);
                return window.ExitCode;
            }
        }
    }

    internal sealed partial class InstallerWindow : Form
    {
        private readonly TextBox installPath = new TextBox();
        private readonly Button install = new Button();
        private readonly Label status = new Label();

        public InstallerWindow(string[] args)
        {
            Text = "安装 MediaFlow";
            AutoScaleMode = AutoScaleMode.Dpi;
            ClientSize = new Size(760, 500);
            MinimumSize = new Size(760, 500);
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            BackColor = Color.FromArgb(246, 247, 250);
            Font = new Font("Microsoft YaHei UI", 10F);
            Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);

            var title = new Label { Text = "MediaFlow", Font = new Font("Microsoft YaHei UI", 28F, FontStyle.Bold), AutoSize = true, Location = new Point(48, 38), ForeColor = Color.FromArgb(35, 38, 50) };
            var subtitle = new Label { Text = "媒体自动化平台", Font = new Font("Microsoft YaHei UI", 12F), AutoSize = true, Location = new Point(52, 98), ForeColor = Color.FromArgb(94, 106, 210) };
            var intro = new Label { Text = "选择程序安装位置。任务数据、截图和日志将在首次启动时单独选择，不会放进程序目录。", AutoSize = false, Size = new Size(652, 52), Location = new Point(52, 148), ForeColor = Color.FromArgb(93, 99, 113) };
            var pathLabel = new Label { Text = "程序安装位置", AutoSize = true, Location = new Point(52, 216), ForeColor = Color.FromArgb(62, 67, 80) };
            installPath.Location = new Point(52, 246);
            installPath.Size = new Size(518, 31);
            installPath.Text = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "MediaFlow");
            var browse = new Button { Text = "选择位置…", Location = new Point(586, 243), Size = new Size(120, 38), FlatStyle = FlatStyle.Flat };
            browse.FlatAppearance.BorderColor = Color.FromArgb(210, 213, 222);
            browse.Click += ChooseFolder;

            var note = new Label { Text = "选择磁盘或上级文件夹时会自动创建 MediaFlow 文件夹。仅支持本地固定磁盘。", AutoSize = false, Size = new Size(652, 44), Location = new Point(52, 294), ForeColor = Color.FromArgb(119, 124, 137) };
            status.AutoSize = false;
            status.Size = new Size(350, 50);
            status.Location = new Point(52, 397);
            status.ForeColor = Color.FromArgb(170, 74, 70);
            var cancel = new Button { Text = "取消", Location = new Point(432, 392), Size = new Size(120, 44), FlatStyle = FlatStyle.Flat, DialogResult = DialogResult.Cancel };
            cancel.FlatAppearance.BorderColor = Color.FromArgb(210, 213, 222);
            install.Text = "开始安装";
            install.Location = new Point(568, 392);
            install.Size = new Size(138, 44);
            install.BackColor = Color.FromArgb(94, 106, 210);
            install.ForeColor = Color.White;
            install.FlatStyle = FlatStyle.Flat;
            install.FlatAppearance.BorderSize = 0;
            install.Click += Install;
            CancelButton = cancel;
            Controls.AddRange(new Control[] { title, subtitle, intro, pathLabel, installPath, browse, note, status, cancel, install });
            ConfigurePrivateInstall(args, cancel);
            Shown += delegate { ActiveControl = install; installPath.SelectionStart = 0; installPath.SelectionLength = 0; };
        }

        private void ChooseFolder(object sender, EventArgs args)
        {
            using (var picker = new FolderBrowserDialog { Description = "选择 MediaFlow 所在的磁盘或上级文件夹", ShowNewFolderButton = true, SelectedPath = installPath.Text })
                if (picker.ShowDialog(this) == DialogResult.OK) installPath.Text = NormalizeInstallPath(picker.SelectedPath, true);
        }

        private static string NormalizeInstallPath(string value, bool selectedParent = false)
        {
            string full = Path.GetFullPath(Environment.ExpandEnvironmentVariables(value.Trim())).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            string root = Path.GetPathRoot(full);
            bool isDriveRoot = String.Equals(full, root.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar), StringComparison.OrdinalIgnoreCase);
            bool alreadyProductFolder = String.Equals(Path.GetFileName(full), "MediaFlow", StringComparison.OrdinalIgnoreCase);
            if (isDriveRoot) return Path.Combine(root, "MediaFlow");
            if (selectedParent && !alreadyProductFolder) return Path.Combine(full, "MediaFlow");
            return full;
        }

        private static string ValidatePath(string value)
        {
            if (String.IsNullOrWhiteSpace(value)) return "请选择程序安装位置。";
            string full;
            try { full = Path.GetFullPath(Environment.ExpandEnvironmentVariables(value.Trim())); }
            catch { return "安装路径格式无效。"; }
            string root = Path.GetPathRoot(full);
            if (String.Equals(full.TrimEnd('\\'), root.TrimEnd('\\'), StringComparison.OrdinalIgnoreCase)) return "不能直接安装到磁盘根目录。";
            string windows = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
            if (full.StartsWith(windows, StringComparison.OrdinalIgnoreCase)) return "不能安装到 Windows 系统目录。";
            if (full.IndexOf(Path.DirectorySeparatorChar + "MediaFlow" + Path.DirectorySeparatorChar + "data", StringComparison.OrdinalIgnoreCase) >= 0) return "程序目录与数据目录必须分开。";
            try
            {
                var drive = new DriveInfo(root);
                if (drive.DriveType != DriveType.Fixed) return "只能安装到本地固定磁盘。";
            }
            catch { return "无法确认安装磁盘类型。"; }
            return null;
        }

        private void Install(object sender, EventArgs args)
        {
            string normalized;
            try { normalized = NormalizeInstallPath(installPath.Text); }
            catch { status.Text = "安装路径格式无效。"; return; }
            installPath.Text = normalized;
            string error = ValidatePath(normalized);
            if (error != null) { status.Text = error; return; }
            install.Enabled = false;
            status.ForeColor = Color.FromArgb(93, 99, 113);
            status.Text = "正在启动安装…";
            bool existingBackgroundPrepared = false;
            bool sameVersionRepair = false;
            try
            {
                string privateManifest = PreparePrivatePayload(normalized);
                string installedVersion = ReadInstalledVersion(normalized);
                string bundledVersion = ReadBundledVersion();
                if (!String.IsNullOrWhiteSpace(installedVersion))
                {
                    int comparison = CompareSemanticVersions(installedVersion, bundledVersion);
                    if (comparison > 0)
                        throw new InvalidOperationException("当前电脑上的 MediaFlow " + installedVersion + " 比安装包 " + bundledVersion + " 更新。为避免误降级，本安装包不会覆盖它。");
                    sameVersionRepair = comparison == 0;
                    if (sameVersionRepair)
                    {
                        string installedRevision = ReadInstalledSourceRevision(normalized);
                        string bundledRevision = ReadBundledSourceRevision();
                        if (String.IsNullOrWhiteSpace(installedRevision) ||
                            String.IsNullOrWhiteSpace(bundledRevision) ||
                            !String.Equals(installedRevision, bundledRevision, StringComparison.OrdinalIgnoreCase))
                            throw new InvalidOperationException("检测到相同版本号但代码身份不同。为避免同号覆盖，请使用版本号更高的安装包；当前程序和用户数据未改动。");
                    }
                    status.Text = sameVersionRepair
                        ? "正在准备同版本修复安装…"
                        : "正在从 " + installedVersion + " 升级到 " + bundledVersion + "…";
                }
                string preparationError;
                if (!PrepareExistingInstallation(normalized, out existingBackgroundPrepared, out preparationError))
                    throw new InvalidOperationException("旧版后台尚未安全停止，安装未开始。\r\n" + preparationError);
                if (sameVersionRepair && !PrepareSameVersionRepair(normalized, out preparationError))
                    throw new InvalidOperationException("同版本修复安装未能准备完成。用户数据没有删除。\r\n" + preparationError);
                if (cancelRequested) throw new OperationCanceledException("已取消；尚未修改程序。");
                string temporaryDirectory = Path.Combine(Path.GetDirectoryName(normalized), ".MediaFlow-install-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(temporaryDirectory);
                string setup = Path.Combine(temporaryDirectory, "MediaFlow-Setup.exe");
                using (Stream source = Assembly.GetExecutingAssembly().GetManifestResourceStream("MediaFlow.Setup.exe"))
                {
                    if (source == null) throw new InvalidOperationException("安装包资源不完整。请重新下载。 ");
                    using (var destination = File.Create(setup)) source.CopyTo(destination);
                }
                string full = normalized;
                var info = new ProcessStartInfo(setup, "--installto \"" + full + "\"" + (quiet ? " --silent" : "")) { UseShellExecute = false, CreateNoWindow = quiet };
                info.EnvironmentVariables["TEMP"] = temporaryDirectory;
                info.EnvironmentVariables["TMP"] = temporaryDirectory;
                string programFiles = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
                if (full.StartsWith(programFiles, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("此每用户安装器请选择非Program Files目录，例如D:\\MediaFlow。");
                RecordStage("software_install", "正在安装软件；用户数据保持原位置");
                Process process = Process.Start(info);
                if (process == null) throw new InvalidOperationException("安装程序未能启动。 ");
                string waitError;
                if (!WaitForExitWithUi(process, 10 * 60 * 1000, out waitError))
                    throw new InvalidOperationException("安装程序等待超时。" + waitError);
                if (process.ExitCode != 0) throw new InvalidOperationException("安装未完成，退出代码：" + process.ExitCode);
                string verificationError;
                if (!VerifyInstalledState(full, out verificationError))
                    throw new InvalidOperationException("安装文件已写入，但启动组件没有准备完成。\r\n" + verificationError + "\r\n可以保留当前目录并再次点击安装进行修复。");
                if (privateManifest != null) ImportPrivateTemplate(full, privateManifest);
                status.ForeColor = Color.FromArgb(42, 135, 82);
                status.Text = "安装完成。首次打开时请选择数据保存位置。";
                ExitCode = 0;
                RecordStage("completed", privateManifest == null ? "软件安装完成" : "软件与私人模板均已完成；旧模板和实例保留");
                install.Text = "完成";
                install.Enabled = true;
                install.Click -= Install;
                install.Click += delegate { Close(); };
            }
            catch (Exception exception)
            {
                ExitCode = exception is OperationCanceledException ? 21 : 20;
                RecordStage("failed", exception.Message);
                string recoveryMessage = null;
                if (existingBackgroundPrepared && !sameVersionRepair) TryRestoreExistingBackground(normalized, out recoveryMessage);
                if (sameVersionRepair && String.IsNullOrWhiteSpace(recoveryMessage))
                    recoveryMessage = "修复安装没有完成，但数据目录仍然保留；请再次运行此安装包。";
                install.Enabled = true;
                status.ForeColor = Color.FromArgb(170, 74, 70);
                status.Text = exception.Message + (String.IsNullOrWhiteSpace(recoveryMessage) ? String.Empty : "\r\n" + recoveryMessage);
            }
        }

        private static string ReadInstalledVersion(string installRoot)
        {
            return ReadInstalledManifestValue(installRoot, "version");
        }

        private static string ReadInstalledSourceRevision(string installRoot)
        {
            return ReadInstalledManifestValue(installRoot, "source_revision");
        }

        private static string ReadInstalledManifestValue(string installRoot, string field)
        {
            string manifest = Path.Combine(installRoot, "current", "release-manifest.json");
            if (!File.Exists(manifest)) return null;
            try
            {
                string pattern = "\\\"" + Regex.Escape(field) + "\\\"\\s*:\\s*\\\"(?<value>[^\\\"]+)\\\"";
                Match match = Regex.Match(File.ReadAllText(manifest), pattern);
                return match.Success ? match.Groups["value"].Value.Trim() : null;
            }
            catch { return null; }
        }

        private static string ReadBundledVersion()
        {
            var attribute = (AssemblyInformationalVersionAttribute)Attribute.GetCustomAttribute(
                Assembly.GetExecutingAssembly(), typeof(AssemblyInformationalVersionAttribute));
            string value = attribute == null ? null : attribute.InformationalVersion;
            if (!String.IsNullOrWhiteSpace(value)) return value.Split('+')[0];
            return Assembly.GetExecutingAssembly().GetName().Version.ToString(3);
        }

        private static string ReadBundledSourceRevision()
        {
            object[] attributes = Assembly.GetExecutingAssembly().GetCustomAttributes(typeof(AssemblyMetadataAttribute), false);
            foreach (object item in attributes)
            {
                var metadata = item as AssemblyMetadataAttribute;
                if (metadata != null && String.Equals(metadata.Key, "SourceRevision", StringComparison.OrdinalIgnoreCase))
                    return metadata.Value;
            }
            return null;
        }

        private static int CompareSemanticVersions(string installed, string bundled)
        {
            string[] leftParts;
            string[] rightParts;
            string leftPrerelease;
            string rightPrerelease;
            if (!TryParseSemanticVersion(installed, out leftParts, out leftPrerelease) ||
                !TryParseSemanticVersion(bundled, out rightParts, out rightPrerelease))
                throw new InvalidOperationException("无法比较已安装版本与安装包版本，请重新下载安装包。");
            for (int index = 0; index < 3; index++)
            {
                int comparison = Int32.Parse(leftParts[index]).CompareTo(Int32.Parse(rightParts[index]));
                if (comparison != 0) return comparison;
            }
            if (leftPrerelease == null && rightPrerelease == null) return 0;
            if (leftPrerelease == null) return 1;
            if (rightPrerelease == null) return -1;
            return ComparePrerelease(leftPrerelease, rightPrerelease);
        }

        private static bool TryParseSemanticVersion(string value, out string[] core, out string prerelease)
        {
            core = null;
            prerelease = null;
            Match match = Regex.Match(value ?? String.Empty, "^(?<core>\\d+\\.\\d+\\.\\d+)(?:-(?<prerelease>[0-9A-Za-z.-]+))?(?:\\+[0-9A-Za-z.-]+)?$");
            if (!match.Success) return false;
            core = match.Groups["core"].Value.Split('.');
            prerelease = match.Groups["prerelease"].Success ? match.Groups["prerelease"].Value : null;
            return true;
        }

        private static int ComparePrerelease(string left, string right)
        {
            string[] leftParts = left.Split('.');
            string[] rightParts = right.Split('.');
            int count = Math.Max(leftParts.Length, rightParts.Length);
            for (int index = 0; index < count; index++)
            {
                if (index >= leftParts.Length) return -1;
                if (index >= rightParts.Length) return 1;
                int leftNumber;
                int rightNumber;
                bool leftNumeric = Int32.TryParse(leftParts[index], out leftNumber);
                bool rightNumeric = Int32.TryParse(rightParts[index], out rightNumber);
                int comparison;
                if (leftNumeric && rightNumeric) comparison = leftNumber.CompareTo(rightNumber);
                else if (leftNumeric) comparison = -1;
                else if (rightNumeric) comparison = 1;
                else comparison = String.Compare(leftParts[index], rightParts[index], StringComparison.Ordinal);
                if (comparison != 0) return comparison;
            }
            return 0;
        }

        private static bool PrepareSameVersionRepair(string installRoot, out string error)
        {
            error = null;
            string updater = Path.Combine(installRoot, "Update.exe");
            if (!File.Exists(updater))
            {
                error = "没有找到现有安装的修复组件。";
                return false;
            }
            if (!RunHidden(updater, "uninstall --silent", out error, 120000)) return false;
            for (int attempt = 0; attempt < 40; attempt++)
            {
                if (!Directory.Exists(Path.Combine(installRoot, "current"))) return true;
                Thread.Sleep(250);
            }
            error = "旧版程序仍被占用，请关闭 MediaFlow 窗口后重试。";
            return false;
        }

        private static bool PrepareExistingInstallation(string installRoot, out bool prepared, out string error)
        {
            prepared = false;
            error = null;
            string current = Path.Combine(installRoot, "current");
            string manage = Path.Combine(current, "manage-mediaflow.ps1");
            string removeBackground = Path.Combine(current, "uninstall-mediaflow-background.ps1");
            if (!File.Exists(manage) && !File.Exists(removeBackground)) return true;

            string powershell = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
            string commandError;
            if (File.Exists(manage) && !RunHidden(powershell, "-NoProfile -ExecutionPolicy Bypass -File " + QuoteArgument(manage) + " -Action Stop -NoBrowser", out commandError))
            {
                error = "停止旧版后台失败：" + commandError;
                return false;
            }
            if (File.Exists(removeBackground) && !RunHidden(powershell, "-NoProfile -ExecutionPolicy Bypass -File " + QuoteArgument(removeBackground) + " -KeepShortcut", out commandError))
            {
                error = "暂时注销旧版后台启动项失败：" + commandError;
                return false;
            }
            prepared = true;
            return true;
        }

        private static void TryRestoreExistingBackground(string installRoot, out string message)
        {
            message = null;
            string installBackground = Path.Combine(installRoot, "current", "install-mediaflow-background.ps1");
            if (!File.Exists(installBackground))
            {
                message = "旧版程序文件已发生变化，未自动恢复后台；请保留现场后重试安装。";
                return;
            }
            string powershell = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
            string restoreError;
            if (!RunHidden(powershell, "-NoProfile -ExecutionPolicy Bypass -File " + QuoteArgument(installBackground) + " -NoShortcut", out restoreError))
                message = "旧版后台自动恢复失败：" + restoreError;
            else
                message = "安装未完成，旧版后台已恢复。";
        }

        private static string QuoteArgument(string value)
        {
            return "\"" + value.Replace("\"", "\\\"") + "\"";
        }

        private static bool VerifyInstalledState(string installRoot, out string error)
        {
            error = null;
            string currentLauncher = Path.Combine(installRoot, "current", "MediaFlow.exe");
            string rootLauncher = Path.Combine(installRoot, "MediaFlow.exe");
            string runtimePython = Path.Combine(installRoot, "current", "runtime", "python", "python.exe");
            if (!File.Exists(currentLauncher) || !File.Exists(rootLauncher) || !File.Exists(runtimePython))
            {
                error = "安装目录中的桌面程序或运行环境不完整。";
                return false;
            }
            if (ScheduledTaskExists()) return true;

            string repairError;
            if (!RunHidden(currentLauncher, "--veloapp-install repair", out repairError))
            {
                error = "后台启动项注册失败：" + repairError;
                return false;
            }
            if (!ScheduledTaskExists())
            {
                error = "后台启动项仍未注册。";
                return false;
            }
            return true;
        }

        private static bool ScheduledTaskExists()
        {
            string ignored;
            return RunHidden(Path.Combine(Environment.SystemDirectory, "schtasks.exe"), "/Query /TN \"MediaFlow Background\"", out ignored);
        }

        private static bool WaitForExitWithUi(Process process, int timeoutMilliseconds, out string error)
        {
            Stopwatch timer = Stopwatch.StartNew();
            while (!process.WaitForExit(200))
            {
                Application.DoEvents();
                if (timer.ElapsedMilliseconds < timeoutMilliseconds) continue;
                try { process.Kill(); }
                catch { }
                error = "已停止等待，避免安装界面永久卡住。";
                return false;
            }
            error = null;
            return true;
        }

        private static bool RunHidden(string fileName, string arguments, out string error, int timeoutMilliseconds = 60000)
        {
            try
            {
                var info = new ProcessStartInfo(fileName, arguments)
                {
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true
                };
                using (Process process = Process.Start(info))
                {
                    if (process == null) { error = "进程未能启动。"; return false; }
                    var output = new StringBuilder();
                    var stderr = new StringBuilder();
                    process.OutputDataReceived += delegate(object source, DataReceivedEventArgs item) { if (item.Data != null) output.AppendLine(item.Data); };
                    process.ErrorDataReceived += delegate(object source, DataReceivedEventArgs item) { if (item.Data != null) stderr.AppendLine(item.Data); };
                    process.BeginOutputReadLine();
                    process.BeginErrorReadLine();
                    string waitError;
                    if (!WaitForExitWithUi(process, timeoutMilliseconds, out waitError))
                    {
                        error = "命令执行超时。" + waitError;
                        return false;
                    }
                    process.WaitForExit();
                    error = stderr.Length == 0 ? output.ToString().Trim() : stderr.ToString().Trim();
                    return process.ExitCode == 0;
                }
            }
            catch (Exception exception)
            {
                error = exception.Message;
                return false;
            }
        }
    }
}
