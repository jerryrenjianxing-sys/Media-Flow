using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Security.Cryptography.X509Certificates;
using System.Text.RegularExpressions;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

[assembly: AssemblyTitle("MediaFlow")]
[assembly: AssemblyDescription("MediaFlow 媒体自动化平台")]
[assembly: AssemblyCompany("MediaFlow")]
[assembly: AssemblyProduct("MediaFlow")]
[assembly: AssemblyVersion("0.4.1.0")]
[assembly: AssemblyFileVersion("0.4.1.0")]

internal static class MediaFlowLauncher
{
    private const string ProductName = "MediaFlow";
    private const string DefaultUrl = "http://127.0.0.1:3000/";
    private const string RuntimeDownloadUrl = "https://go.microsoft.com/fwlink/p/?LinkId=2124703";

    [STAThread]
    private static int Main(string[] args)
    {
        string projectRoot = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        bool distribution = File.Exists(Path.Combine(projectRoot, "runtime", "python", "python.exe"));
        bool veloHook = args.Length > 0 && args[0].StartsWith("--veloapp-", StringComparison.OrdinalIgnoreCase);
        bool backgroundRun = HasArgument(args, "--background-run");
        string dataRoot = ResolveDataRoot(projectRoot, distribution, !veloHook && !backgroundRun);
        if (dataRoot == null) return 0;
        ConfigureEnvironment(projectRoot, distribution, dataRoot);
        if (args.Length > 0 && args[0].StartsWith("--veloapp-", StringComparison.OrdinalIgnoreCase)) return HandleVelopackHook(projectRoot, args[0]);
        if (HasArgument(args, "--background-run")) return RunBackgroundHost(projectRoot, distribution);

        string scriptPath = ExistingPath(projectRoot, "run-mediaflow-console.ps1", "run-riskflow-console.ps1");
        if (!File.Exists(scriptPath))
        {
            MessageBox.Show("没有找到 MediaFlow 启动文件。请重新安装后再打开。", ProductName, MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }

        if (HasArgument(args, "--background-only"))
        {
            string output;
            string error;
            try { return RunPowerShell(projectRoot, scriptPath, "-NoBrowser", out output, out error, 60000); }
            catch { return 1; }
        }

        bool ownsMutex;
        using (var mutex = new Mutex(true, "Local\\RiskFlow.Desktop.Singleton", out ownsMutex))
        {
            if (!ownsMutex)
            {
                try { EventWaitHandle.OpenExisting("Local\\RiskFlow.Desktop.Activate").Set(); } catch { }
                return 0;
            }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            string pageUrl = ArgumentValue(args, "--dev-url=") ?? DefaultUrl;
            using (var activate = new EventWaitHandle(false, EventResetMode.AutoReset, "Local\\RiskFlow.Desktop.Activate"))
            using (var form = new MediaFlowForm(projectRoot, dataRoot, pageUrl, activate, scriptPath)) Application.Run(form);
        }
        return 0;
    }

    private static bool HasArgument(string[] args, string expected) { return Array.Exists(args, value => string.Equals(value, expected, StringComparison.OrdinalIgnoreCase)); }
    private static string ArgumentValue(string[] args, string prefix)
    {
        string value = Array.Find(args, item => item.StartsWith(prefix, StringComparison.OrdinalIgnoreCase));
        return value == null ? null : value.Substring(prefix.Length).Trim();
    }

    private static string ExistingPath(string root, string preferred, string legacy)
    {
        string preferredPath = Path.Combine(root, preferred);
        return File.Exists(preferredPath) ? preferredPath : Path.Combine(root, legacy);
    }

    private static string ResolveDataRoot(string projectRoot, bool distribution, bool interactive)
    {
        if (!distribution) return Path.Combine(projectRoot, "fixed_runner", "runtime");
        string configured = Environment.GetEnvironmentVariable("MEDIAFLOW_DATA_ROOT");
        if (!string.IsNullOrWhiteSpace(configured)) return configured;
        string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        string pendingMigration = Path.Combine(local, ProductName, "pending-storage-migration.json");
        if (File.Exists(pendingMigration))
        {
            string migrationMessage;
            bool migrated = ApplyPendingStorageMigration(projectRoot, out migrationMessage);
            if (!migrated && interactive)
                MessageBox.Show("数据目录迁移没有完成，MediaFlow将继续使用原目录；原数据未被删除。\n\n" + migrationMessage, ProductName, MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
        string bootstrap = Path.Combine(local, ProductName, "bootstrap.json");
        if (File.Exists(bootstrap))
        {
            try
            {
                Match match = Regex.Match(File.ReadAllText(bootstrap, Encoding.UTF8), "\\\"data_root\\\"\\s*:\\s*\\\"(?<path>(?:\\\\.|[^\\\"])*)\\\"");
                if (match.Success) return Regex.Unescape(match.Groups["path"].Value);
            }
            catch { }
        }
        string defaultRoot = Path.Combine(local, ProductName, "data");
        if (!interactive)
        {
            Directory.CreateDirectory(defaultRoot);
            return defaultRoot;
        }
        string legacyRoot = Path.Combine(local, "RiskFlow", "data");
        using (var dialog = new DataLocationDialog(defaultRoot, Directory.Exists(legacyRoot)))
        {
            if (dialog.ShowDialog() != DialogResult.OK) return null;
            string error;
            if (!RunStorageSetup(projectRoot, dialog.SelectedPath, dialog.MigrateLegacy, out error))
            {
                if (Directory.Exists(legacyRoot))
                {
                    string fallbackError;
                    if (RunStorageSetup(projectRoot, legacyRoot, false, out fallbackError))
                    {
                        MessageBox.Show("新目录校验失败，MediaFlow将继续使用原有数据；原目录没有被删除或覆盖。\n\n" + error, ProductName, MessageBoxButtons.OK, MessageBoxIcon.Warning);
                        return legacyRoot;
                    }
                }
                MessageBox.Show("数据目录准备失败，原有数据没有被删除。\n\n" + error, ProductName, MessageBoxButtons.OK, MessageBoxIcon.Error);
                return null;
            }
            return dialog.SelectedPath;
        }
    }

    private static bool RunStorageSetup(string projectRoot, string dataRoot, bool migrateLegacy, out string error)
    {
        string python = Path.Combine(projectRoot, "runtime", "python", "python.exe");
        string script = Path.Combine(projectRoot, "fixed_runner", "storage_setup.py");
        var startInfo = new ProcessStartInfo
        {
            FileName = python,
            Arguments = "\"" + script + "\" prepare --target \"" + dataRoot + "\" --app-root \"" + projectRoot + "\"" + (migrateLegacy ? "" : " --no-migrate"),
            WorkingDirectory = Path.Combine(projectRoot, "fixed_runner"),
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardError = true,
            RedirectStandardOutput = true,
            StandardErrorEncoding = Encoding.UTF8,
            StandardOutputEncoding = Encoding.UTF8,
        };
        try
        {
            string output;
            string stderr;
            int code = RunProcessCapture(startInfo, 120000, out output, out stderr);
            error = string.IsNullOrWhiteSpace(stderr) ? output : stderr;
            return code == 0;
        }
        catch (Exception exception) { error = exception.Message; return false; }
    }

    private static bool ApplyPendingStorageMigration(string projectRoot, out string message)
    {
        string python = Path.Combine(projectRoot, "runtime", "python", "python.exe");
        string script = Path.Combine(projectRoot, "fixed_runner", "storage_setup.py");
        var startInfo = new ProcessStartInfo
        {
            FileName = python,
            Arguments = "\"" + script + "\" apply-pending --app-root \"" + projectRoot + "\"",
            WorkingDirectory = Path.Combine(projectRoot, "fixed_runner"),
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardError = true,
            RedirectStandardOutput = true,
            StandardErrorEncoding = Encoding.UTF8,
            StandardOutputEncoding = Encoding.UTF8,
        };
        try
        {
            string output;
            string error;
            int code = RunProcessCapture(startInfo, 120000, out output, out error);
            message = string.IsNullOrWhiteSpace(error) ? output : error;
            return code == 0 && output.IndexOf("\"applied\": true", StringComparison.OrdinalIgnoreCase) >= 0;
        }
        catch (Exception exception) { message = exception.Message; return false; }
    }

    private static void ConfigureEnvironment(string projectRoot, bool distribution, string dataRoot)
    {
        Environment.SetEnvironmentVariable("PYTHONUTF8", "1");
        Environment.SetEnvironmentVariable("PYTHONIOENCODING", "utf-8");
        if (!distribution) return;
        Directory.CreateDirectory(dataRoot);
        Environment.SetEnvironmentVariable("MEDIAFLOW_APP_ROOT", projectRoot);
        Environment.SetEnvironmentVariable("MEDIAFLOW_DATA_ROOT", dataRoot);
        // Compatibility aliases keep the first MediaFlow release on the existing update/runtime chain.
        Environment.SetEnvironmentVariable("RISKFLOW_APP_ROOT", projectRoot);
        Environment.SetEnvironmentVariable("RISKFLOW_DATA_ROOT", dataRoot);
        string tools = Path.Combine(projectRoot, "runtime", "platform-tools") + ";" + Path.Combine(projectRoot, "runtime", "node");
        Environment.SetEnvironmentVariable("PATH", tools + ";" + (Environment.GetEnvironmentVariable("PATH") ?? string.Empty));
    }

    private static int HandleVelopackHook(string projectRoot, string hook)
    {
        string script;
        string arguments;
        if (string.Equals(hook, "--veloapp-uninstall", StringComparison.OrdinalIgnoreCase))
        {
            script = ExistingPath(projectRoot, "uninstall-mediaflow-background.ps1", "uninstall-riskflow-background.ps1");
            arguments = "-KeepShortcut";
        }
        else if (string.Equals(hook, "--veloapp-install", StringComparison.OrdinalIgnoreCase) || string.Equals(hook, "--veloapp-updated", StringComparison.OrdinalIgnoreCase))
        {
            script = ExistingPath(projectRoot, "install-mediaflow-background.ps1", "install-riskflow-background.ps1");
            arguments = "-NoStart -NoShortcut";
        }
        else return 0;
        if (!File.Exists(script)) return 1;
        string output;
        string error;
        try { return RunPowerShell(projectRoot, script, arguments, out output, out error); } catch { return 1; }
    }

    private static int RunBackgroundHost(string projectRoot, bool distribution)
    {
        string python = distribution ? Path.Combine(projectRoot, "runtime", "python", "python.exe") : Path.Combine(projectRoot, ".venv", "Scripts", "python.exe");
        string host = Path.Combine(projectRoot, "fixed_runner", "background_host.py");
        if (!File.Exists(python) || !File.Exists(host)) return 1;
        var startInfo = new ProcessStartInfo { FileName = python, Arguments = "\"" + host + "\" run", WorkingDirectory = Path.Combine(projectRoot, "fixed_runner"), UseShellExecute = false, CreateNoWindow = true, WindowStyle = ProcessWindowStyle.Hidden };
        using (Process process = Process.Start(startInfo)) { process.WaitForExit(); return process.ExitCode; }
    }

    private static int RunProcessCapture(ProcessStartInfo startInfo, int timeoutMilliseconds, out string standardOutput, out string standardError)
    {
        var output = new StringBuilder();
        var error = new StringBuilder();
        using (Process process = new Process { StartInfo = startInfo })
        {
            process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs eventArgs) { if (eventArgs.Data != null) output.AppendLine(eventArgs.Data); };
            process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs eventArgs) { if (eventArgs.Data != null) error.AppendLine(eventArgs.Data); };
            if (!process.Start()) throw new InvalidOperationException("无法启动后台命令");
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            if (!process.WaitForExit(timeoutMilliseconds))
            {
                try { process.Kill(); } catch { }
                throw new TimeoutException("后台命令等待超时");
            }
            process.WaitForExit();
            standardOutput = output.ToString();
            standardError = error.ToString();
            return process.ExitCode;
        }
    }

    internal static int RunPowerShell(string projectRoot, string scriptPath, string scriptArguments, out string standardOutput, out string standardError, int timeoutMilliseconds = 60000)
    {
        string escapedScriptPath = scriptPath.Replace("'", "''");
        string command = "$ProgressPreference='SilentlyContinue';$utf8=[System.Text.UTF8Encoding]::new($false);[Console]::OutputEncoding=$utf8;$OutputEncoding=$utf8;& '" + escapedScriptPath + "'" + (string.IsNullOrWhiteSpace(scriptArguments) ? string.Empty : " " + scriptArguments);
        string encodedCommand = Convert.ToBase64String(Encoding.Unicode.GetBytes(command));
        string arguments = "-NoProfile -ExecutionPolicy Bypass -EncodedCommand " + encodedCommand;
        var startInfo = new ProcessStartInfo
        {
            FileName = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
            Arguments = arguments,
            WorkingDirectory = projectRoot,
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden,
            RedirectStandardError = true,
            RedirectStandardOutput = true,
            StandardErrorEncoding = Encoding.UTF8,
            StandardOutputEncoding = Encoding.UTF8
        };
        return RunProcessCapture(startInfo, timeoutMilliseconds, out standardOutput, out standardError);
    }

    internal static void OpenExternal(string url) { Process.Start(new ProcessStartInfo { FileName = url, UseShellExecute = true }); }

    private sealed class MediaFlowForm : Form
    {
        private readonly string projectRoot;
        private readonly string dataRoot;
        private readonly string pageUrl;
        private readonly string startupScript;
        private readonly WebView2 webView;
        private readonly Panel fallback;
        private readonly NotifyIcon tray;
        private readonly EventWaitHandle activate;
        private bool allowExit;
        private string selectedMuMuInstaller;
        private bool startupRunning;

        internal MediaFlowForm(string projectRoot, string dataRoot, string pageUrl, EventWaitHandle activate, string startupScript)
        {
            this.projectRoot = projectRoot;
            this.dataRoot = dataRoot;
            this.pageUrl = pageUrl;
            this.activate = activate;
            this.startupScript = startupScript;
            AutoScaleMode = AutoScaleMode.Dpi;
            Text = "MediaFlow 媒体自动化平台";
            StartPosition = FormStartPosition.CenterScreen;
            MinimumSize = new Size(1024, 700);
            Size = new Size(1440, 920);
            Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            webView = new WebView2 { Dock = DockStyle.Fill, Visible = false };
            fallback = BuildFallback();
            Controls.Add(webView);
            Controls.Add(fallback);

            var menu = new ContextMenuStrip();
            menu.Items.Add("打开 MediaFlow", null, delegate { RestoreWindow(); });
            menu.Items.Add("在浏览器中打开", null, delegate { OpenExternal(pageUrl); });
            menu.Items.Add("打开诊断目录", null, delegate { OpenDiagnostics(); });
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add("停止后台服务", null, delegate { StopBackground(); });
            menu.Items.Add("卸载 MediaFlow…", null, delegate { ConfirmAndUninstall(); });
            menu.Items.Add("退出界面", null, delegate { allowExit = true; Close(); });
            tray = new NotifyIcon { Icon = Icon, Text = "MediaFlow 后台运行中", Visible = true, ContextMenuStrip = menu };
            tray.DoubleClick += delegate { RestoreWindow(); };
            Shown += async delegate { await StartBackgroundAndInitialize(); };
            FormClosing += OnFormClosing;
            Resize += delegate { if (WindowState == FormWindowState.Minimized) Hide(); };
            var activateThread = new Thread(WaitForActivation) { IsBackground = true, Name = "RiskFlow activation listener" };
            activateThread.Start();
        }

        private Panel BuildFallback()
        {
            var panel = new Panel { Dock = DockStyle.Fill, BackColor = Color.FromArgb(245, 246, 247) };
            var title = new Label { AutoSize = false, Text = "正在打开 MediaFlow…", Font = new Font("Microsoft YaHei UI", 20F, FontStyle.Bold), TextAlign = ContentAlignment.BottomCenter, Dock = DockStyle.Top, Height = 260 };
            var detail = new Label { Name = "Detail", AutoSize = false, Text = "桌面窗口会加载与浏览器完全相同的本机控制台。", Font = new Font("Microsoft YaHei UI", 10F), ForeColor = Color.DimGray, TextAlign = ContentAlignment.TopCenter, Dock = DockStyle.Top, Height = 62 };
            var actions = new FlowLayoutPanel { Dock = DockStyle.Top, Height = 54, FlowDirection = FlowDirection.LeftToRight, WrapContents = false };
            var install = new Button { Text = "安装 WebView2 运行时", AutoSize = true, Height = 36, Margin = new Padding(8) };
            var browser = new Button { Text = "在浏览器中打开", AutoSize = true, Height = 36, Margin = new Padding(8) };
            var diagnostics = new Button { Text = "查看诊断", AutoSize = true, Height = 36, Margin = new Padding(8) };
            var retry = new Button { Name = "Retry", Text = "重试启动", AutoSize = true, Height = 36, Margin = new Padding(8), Visible = false };
            install.Click += delegate { OpenExternal(RuntimeDownloadUrl); };
            browser.Click += delegate { OpenExternal(pageUrl); };
            diagnostics.Click += delegate { OpenDiagnostics(); };
            retry.Click += async delegate { await StartBackgroundAndInitialize(); };
            actions.Controls.Add(install);
            actions.Controls.Add(browser);
            actions.Controls.Add(diagnostics);
            actions.Controls.Add(retry);
            actions.Layout += delegate { actions.Padding = new Padding(Math.Max(0, (actions.ClientSize.Width - install.Width - browser.Width - diagnostics.Width - retry.Width - 64) / 2), 0, 0, 0); };
            panel.Controls.Add(actions);
            panel.Controls.Add(detail);
            panel.Controls.Add(title);
            return panel;
        }

        private async Task StartBackgroundAndInitialize()
        {
            if (startupRunning) return;
            startupRunning = true;
            Control retry = fallback.Controls.Find("Retry", true)[0];
            retry.Visible = false;
            ShowFallback("正在启动本机后台服务；桌面窗口已经可用。最多等待60秒。", false);
            try
            {
                string output = null;
                string error = null;
                int code = await Task.Run(delegate { return RunPowerShell(projectRoot, startupScript, "-NoBrowser", out output, out error, 60000); });
                if (code != 0)
                {
                    string details = string.IsNullOrWhiteSpace(error) ? output : error;
                    throw new InvalidOperationException(string.IsNullOrWhiteSpace(details) ? "后台启动失败" : details.Trim());
                }
                await InitializeWebView();
            }
            catch (TimeoutException)
            {
                ShowFallback("后台启动超过60秒，已经停止等待。可重试、查看诊断，或先使用浏览器入口。", true);
            }
            catch (Exception exception)
            {
                ShowFallback("后台启动失败：" + exception.Message + "\n可重试或先使用浏览器入口。", true);
            }
            finally { startupRunning = false; }
        }

        private async Task InitializeWebView()
        {
            try
            {
                await webView.EnsureCoreWebView2Async(null);
                webView.CoreWebView2.Settings.AreDefaultContextMenusEnabled = false;
                webView.CoreWebView2.Settings.IsStatusBarEnabled = false;
                webView.CoreWebView2.Settings.AreDevToolsEnabled = !File.Exists(Path.Combine(projectRoot, "runtime", "python", "python.exe"));
                webView.CoreWebView2.WebMessageReceived += OnWebMessageReceived;
                webView.ZoomFactor = 1.0;
                string separator = pageUrl.Contains("?") ? "&" : "?";
                webView.Source = new Uri(pageUrl + separator + "desktop_launch=" + DateTimeOffset.UtcNow.ToUnixTimeMilliseconds());
                fallback.Visible = false;
                webView.Visible = true;
                webView.BringToFront();
            }
            catch (WebView2RuntimeNotFoundException) { ShowFallback("这台电脑缺少 Microsoft WebView2 Runtime。安装后重新打开 MediaFlow；浏览器入口仍可正常使用。"); }
            catch (Exception exception) { ShowFallback("桌面页面加载失败：" + exception.Message + "\n可先使用浏览器入口继续工作。"); }
        }

        private void OnWebMessageReceived(object sender, CoreWebView2WebMessageReceivedEventArgs eventArgs)
        {
            string message;
            try { message = eventArgs.TryGetWebMessageAsString(); }
            catch { return; }
            if (message == "mediaflow:choose-folder")
            {
                using (var picker = new FolderBrowserDialog { Description = "选择文件夹", ShowNewFolderButton = true })
                    if (picker.ShowDialog(this) == DialogResult.OK) PostDesktopSelection("folder-selected", picker.SelectedPath, null);
                return;
            }
            if (message == "mediaflow:choose-mumu-installer")
            {
                using (var picker = new OpenFileDialog { Title = "选择已下载的MuMu官方安装程序", Filter = "MuMu安装程序 (*.exe)|*.exe", CheckFileExists = true, Multiselect = false })
                {
                    if (picker.ShowDialog(this) != DialogResult.OK) return;
                    string error;
                    if (!ValidateMuMuInstaller(picker.FileName, out error)) { PostDesktopSelection("mumu-installer-rejected", null, error); return; }
                    selectedMuMuInstaller = picker.FileName;
                    PostDesktopSelection("mumu-installer-selected", picker.FileName, null);
                }
                return;
            }
            if (message == "mediaflow:launch-mumu-installer" && !string.IsNullOrWhiteSpace(selectedMuMuInstaller))
            {
                string error;
                if (!ValidateMuMuInstaller(selectedMuMuInstaller, out error)) { PostDesktopSelection("mumu-installer-rejected", null, error); return; }
                try
                {
                    Process.Start(new ProcessStartInfo { FileName = selectedMuMuInstaller, UseShellExecute = true });
                    PostDesktopSelection("mumu-installer-started", selectedMuMuInstaller, null);
                }
                catch (Exception exception) { PostDesktopSelection("mumu-installer-rejected", null, exception.Message); }
            }
        }

        private static bool ValidateMuMuInstaller(string path, out string error)
        {
            error = null;
            if (!File.Exists(path) || !string.Equals(Path.GetExtension(path), ".exe", StringComparison.OrdinalIgnoreCase) || Path.GetFileName(path).IndexOf("mumu", StringComparison.OrdinalIgnoreCase) < 0)
            {
                error = "请选择从MuMu官网下载的EXE安装程序。";
                return false;
            }
            try
            {
                var certificate = new X509Certificate2(X509Certificate.CreateFromSignedFile(path));
                string subject = certificate.Subject ?? "";
                if (subject.IndexOf("NetEase", StringComparison.OrdinalIgnoreCase) < 0 && subject.IndexOf("MuMu", StringComparison.OrdinalIgnoreCase) < 0)
                {
                    error = "安装程序的数字签名发布者不是已识别的MuMu/NetEase。";
                    return false;
                }
            }
            catch
            {
                error = "安装程序没有可验证的数字签名，不会启动。";
                return false;
            }
            return true;
        }

        private void PostDesktopSelection(string type, string path, string error)
        {
            if (webView.CoreWebView2 == null) return;
            Func<string, string> encode = value => value == null ? "null" : "\"" + value.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", "").Replace("\n", "\\n") + "\"";
            webView.CoreWebView2.PostWebMessageAsJson("{\"type\":" + encode(type) + ",\"path\":" + encode(path) + ",\"error\":" + encode(error) + "}");
        }

        private void ShowFallback(string message, bool allowRetry = true)
        {
            Control detail = fallback.Controls.Find("Detail", true)[0];
            detail.Text = message;
            Control retry = fallback.Controls.Find("Retry", true)[0];
            retry.Visible = allowRetry;
            webView.Visible = false;
            fallback.Visible = true;
            fallback.BringToFront();
        }

        private void WaitForActivation()
        {
            while (!IsDisposed)
            {
                try { activate.WaitOne(); if (!IsDisposed) BeginInvoke((MethodInvoker)RestoreWindow); }
                catch { return; }
            }
        }

        private void RestoreWindow()
        {
            Show();
            if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
            Activate();
            BringToFront();
        }

        private void StopBackground()
        {
            string output;
            string error;
            try
            {
                int code = RunPowerShell(projectRoot, ExistingPath(projectRoot, "manage-mediaflow.ps1", "manage-riskflow.ps1"), "-Action Stop -NoBrowser", out output, out error);
                MessageBox.Show(code == 0 ? "后台服务已停止。" : "后台服务未能停止：\n" + (string.IsNullOrWhiteSpace(error) ? output : error), ProductName, MessageBoxButtons.OK, code == 0 ? MessageBoxIcon.Information : MessageBoxIcon.Warning);
            }
            catch (Exception exception) { MessageBox.Show("后台服务未能停止：\n" + exception.Message, ProductName, MessageBoxButtons.OK, MessageBoxIcon.Warning); }
        }

        private void OpenDiagnostics()
        {
            try
            {
                Directory.CreateDirectory(dataRoot);
                Process.Start(new ProcessStartInfo { FileName = "explorer.exe", Arguments = "\"" + dataRoot + "\"", UseShellExecute = true });
            }
            catch (Exception exception)
            {
                MessageBox.Show("无法打开诊断目录：\n" + exception.Message, ProductName, MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        }

        private void ConfirmAndUninstall()
        {
            DialogResult answer = MessageBox.Show(
                "确定要卸载 MediaFlow 吗？\n\n程序文件将被删除；任务数据、截图、日志、设备档案和MuMu虚拟机都会保留。",
                "卸载 MediaFlow",
                MessageBoxButtons.YesNo,
                MessageBoxIcon.Warning,
                MessageBoxDefaultButton.Button2);
            if (answer != DialogResult.Yes) return;

            string updateExe = Path.GetFullPath(Path.Combine(projectRoot, "..", "Update.exe"));
            if (!File.Exists(updateExe))
            {
                MessageBox.Show("没有找到卸载程序。请从Windows“已安装的应用”中卸载MediaFlow。", ProductName, MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }

            string output;
            string error;
            try
            {
                RunPowerShell(projectRoot, ExistingPath(projectRoot, "manage-mediaflow.ps1", "manage-riskflow.ps1"), "-Action Stop -NoBrowser", out output, out error);
                allowExit = true;
                tray.Visible = false;
                Process.Start(new ProcessStartInfo { FileName = updateExe, Arguments = "uninstall --silent", UseShellExecute = true });
                Close();
            }
            catch (Exception exception)
            {
                MessageBox.Show("无法启动卸载：\n" + exception.Message, ProductName, MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        private void OnFormClosing(object sender, FormClosingEventArgs eventArgs)
        {
            if (allowExit || eventArgs.CloseReason == CloseReason.WindowsShutDown) return;
            eventArgs.Cancel = true;
            Hide();
            tray.ShowBalloonTip(1800, ProductName, "界面已收进托盘，后台任务会继续运行。", ToolTipIcon.Info);
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                tray.Visible = false;
                tray.Dispose();
                activate.Set();
            }
            base.Dispose(disposing);
        }
    }

    private sealed class DataLocationDialog : Form
    {
        private readonly TextBox pathBox;
        private readonly CheckBox migrateBox;
        internal string SelectedPath { get { return pathBox.Text.Trim(); } }
        internal bool MigrateLegacy { get { return migrateBox.Checked; } }

        internal DataLocationDialog(string defaultPath, bool legacyAvailable)
        {
            Text = "MediaFlow 首次设置";
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            ClientSize = new Size(620, 305);
            Font = new Font("Microsoft YaHei UI", 10F);
            Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath) ?? SystemIcons.Application;
            var title = new Label { Text = "选择 MediaFlow 数据存储位置", AutoSize = true, Font = new Font(Font, FontStyle.Bold), Location = new Point(28, 24) };
            var detail = new Label { Text = "数据库、任务截图、日志、设备档案和备份都会保存在这里。程序升级不会覆盖此目录。", AutoSize = false, Location = new Point(28, 62), Size = new Size(560, 48), ForeColor = Color.DimGray };
            pathBox = new TextBox { Text = defaultPath, Location = new Point(28, 122), Size = new Size(454, 30) };
            var browse = new Button { Text = "浏览…", Location = new Point(493, 119), Size = new Size(95, 36) };
            browse.Click += delegate
            {
                using (var picker = new FolderBrowserDialog { Description = "选择 MediaFlow 数据目录", SelectedPath = pathBox.Text, ShowNewFolderButton = true })
                    if (picker.ShowDialog(this) == DialogResult.OK) pathBox.Text = picker.SelectedPath;
            };
            migrateBox = new CheckBox { Text = legacyAvailable ? "复制并校验这台电脑上的旧版数据（保留原目录用于回滚）" : "这台电脑上没有检测到旧版数据", Checked = legacyAvailable, Enabled = legacyAvailable, AutoSize = true, Location = new Point(28, 174) };
            var cancel = new Button { Text = "取消", DialogResult = DialogResult.Cancel, Location = new Point(385, 238), Size = new Size(95, 38) };
            var confirm = new Button { Text = "确认并继续", DialogResult = DialogResult.OK, Location = new Point(493, 238), Size = new Size(95, 38) };
            AcceptButton = confirm;
            CancelButton = cancel;
            Controls.AddRange(new Control[] { title, detail, pathBox, browse, migrateBox, cancel, confirm });
        }
    }
}
