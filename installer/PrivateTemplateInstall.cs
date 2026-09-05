using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace MediaFlow.Installation
{
    internal sealed partial class InstallerWindow
    {
        private bool quiet;
        private bool cancelRequested;
        private string logFile;
        private string cancelFile;
        private readonly TextBox templatePath = new TextBox();
        private readonly TextBox mumuPath = new TextBox();
        private readonly TextBox resumeInstance = new TextBox();
        internal int ExitCode = 20;

        internal int RunQuiet() { Install(null, EventArgs.Empty); return ExitCode; }

        private void ConfigurePrivateInstall(string[] args, Button cancel)
        {
            quiet = Array.IndexOf(args, "--quiet") >= 0;
            var options = new Dictionary<string, string>();
            for (int i = 0; i < args.Length; i++)
            {
                if (args[i] == "--quiet") continue;
                if (i + 1 >= args.Length || (args[i] != "--install-dir" && args[i] != "--template-dir"
                    && args[i] != "--log-file" && args[i] != "--mumu-manager" && args[i] != "--resume-instance"))
                    throw new ArgumentException("未知安装参数或缺少参数值。");
                options.Add(args[i], args[++i]);
            }
            if (options.ContainsKey("--install-dir")) installPath.Text = options["--install-dir"];
            templatePath.Text = options.ContainsKey("--template-dir") ? options["--template-dir"] : "";
            mumuPath.Text = options.ContainsKey("--mumu-manager") ? options["--mumu-manager"] : FindMuMuManager();
            logFile = options.ContainsKey("--log-file") ? Path.GetFullPath(options["--log-file"]) : null;
            resumeInstance.Text = options.ContainsKey("--resume-instance") ? options["--resume-instance"] : "";
            ClientSize = new Size(760, 730);
            status.Location = new Point(52, 612); status.Size = new Size(480, 90);
            install.Location = new Point(568, 612); cancel.Location = new Point(432, 558);
            templatePath.Location = new Point(52, 364); templatePath.Size = new Size(652, 31);
            mumuPath.Location = new Point(52, 430); mumuPath.Size = new Size(652, 31);
            resumeInstance.Location = new Point(52, 500); resumeInstance.Size = new Size(652, 31);
            Controls.AddRange(new Control[] {
                new Label { Text = "私人模板目录（留空则使用安装磁盘的 MediaFlowTemplates）", AutoSize = true, Location = new Point(52, 340) }, templatePath,
                new Label { Text = "MuMu管理程序（私人模板导入需要确认实际存储盘）", AutoSize = true, Location = new Point(52, 407) }, mumuPath,
                new Label { Text = "接续核验实例号（通常留空；只核验上次导入现场，不重新导入）", AutoSize = true, Location = new Point(52, 477) }, resumeInstance });
            cancel.DialogResult = DialogResult.None;
            cancel.Click += delegate {
                if (install.Enabled) { Close(); return; }
                cancelRequested = true;
                if (cancelFile != null) File.WriteAllText(cancelFile, "cancel");
                status.Text = "已请求取消；当前MuMu命令结束后安全停止，不删除现场，请勿重复安装。";
            };
            FormClosing += delegate(object sender, FormClosingEventArgs e) {
                if (!install.Enabled) { e.Cancel = true; cancelRequested = true;
                    if (cancelFile != null) File.WriteAllText(cancelFile, "cancel"); }
            };
        }

        private static string FindMuMuManager()
        {
            foreach (var drive in DriveInfo.GetDrives())
            {
                if (drive.DriveType != DriveType.Fixed || !drive.IsReady) continue;
                foreach (string folder in new[] { "MuMuPlayer", "Program Files\\Netease\\MuMu", "Program Files\\Netease\\MuMuPlayer", "Program Files\\MuMuPlayer" })
                {
                    string path = Path.Combine(drive.RootDirectory.FullName, folder, "nx_main", "MuMuManager.exe");
                    if (File.Exists(path)) return path;
                }
            }
            return ""; // Do not guess a non-default storage location.
        }

        private void RecordStage(string stage, string message)
        {
            status.Text = message;
            if (logFile != null)
            {
                Directory.CreateDirectory(Path.GetDirectoryName(logFile));
                var record = new Dictionary<string, object> { { "stage", stage }, { "message", message },
                    { "at", DateTimeOffset.Now.ToString("o") }, { "exit_code", ExitCode } };
                File.AppendAllText(logFile, new JavaScriptSerializer().Serialize(record) + Environment.NewLine, new UTF8Encoding(false));
            }
            Application.DoEvents();
        }

        private string PreparePrivatePayload(string installDirectory)
        {
            bool required = false;
            foreach (AssemblyMetadataAttribute item in Assembly.GetExecutingAssembly().GetCustomAttributes(typeof(AssemblyMetadataAttribute), false))
                if (item.Key == "PrivateTemplateExpected" && item.Value == "true") required = true;
            var payload = PrivateTemplatePayload.Read(Application.ExecutablePath, required);
            if (payload == null) return null;
            if (resumeInstance.Text.Length > 0 && !System.Text.RegularExpressions.Regex.IsMatch(resumeInstance.Text, "^[0-9]+$"))
                throw new ArgumentException("接续实例号只能填写数字；留空表示首次导入。");
            string directory = String.IsNullOrWhiteSpace(templatePath.Text)
                ? Path.Combine(Path.GetPathRoot(installDirectory), "MediaFlowTemplates") : Path.GetFullPath(templatePath.Text);
            templatePath.Text = directory;
            string manager = Path.GetFullPath(mumuPath.Text);
            if (!File.Exists(manager) || !String.Equals(Path.GetFileName(manager), "MuMuManager.exe", StringComparison.OrdinalIgnoreCase))
                throw new IOException("请指定实际的MuMuManager.exe；没有确认存储位置，不会安装模板。");
            string storage = Path.Combine(Path.GetDirectoryName(Path.GetDirectoryName(manager)), "vms");
            if (!Directory.Exists(storage)) throw new IOException("MuMu实际磁盘目录未确认，请检查管理程序位置。");
            payload.RequireSpace(directory, installDirectory, storage);
            if (!quiet && MessageBox.Show(this, "此私人快照可能包含退出账号后残留的缓存和账号标识，不是公共干净模板。仅供两台自有电脑测试，不可公开分发。继续吗？", "私人模板", MessageBoxButtons.OKCancel) != DialogResult.OK)
                throw new OperationCanceledException("已取消私人模板安装。");
            RecordStage("template_extract", "正在校验并提取私人模板到指定磁盘；尚未覆盖软件");
            string manifest = payload.Extract(directory, p => RecordStage("template_extract", "私人模板提取与校验 " + p + "%"), () => cancelRequested);
            cancelFile = Path.Combine(Path.GetDirectoryName(manifest), "cancel-" + Guid.NewGuid().ToString("N"));
            return manifest;
        }

        private void ImportPrivateTemplate(string installed, string manifest)
        {
            if (cancelRequested) throw new OperationCanceledException("软件已安装；模板尚未导入，原默认不变。可重新安装继续。");
            string root = Path.Combine(installed, "current");
            string python = Path.Combine(root, "runtime", "python", "python.exe");
            string script = Path.Combine(root, "fixed_runner", "private_template_cli.py");
            string result = Path.Combine(Path.GetDirectoryName(manifest), "import-result.json");
            if (!File.Exists(script) || !File.Exists(python)) throw new IOException("安装版模板组件不完整，未导入模板。");
            RecordStage("template_import", "正在导入并验证私人模板；请保持业务队列暂停，可安全请求取消");
            var info = new ProcessStartInfo(python, QuoteArgument(script) + " --manifest " + QuoteArgument(manifest)
                + " --result " + QuoteArgument(result) + " --mumu-manager " + QuoteArgument(mumuPath.Text)
                + " --cancel-file " + QuoteArgument(cancelFile)
                + (resumeInstance.Text.Length > 0 ? " --resume-instance " + QuoteArgument(resumeInstance.Text) : "")) {
                UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = root };
            info.EnvironmentVariables["TEMP"] = Path.GetDirectoryName(manifest);
            info.EnvironmentVariables["TMP"] = Path.GetDirectoryName(manifest);
            info.EnvironmentVariables["PYTHONUTF8"] = "1";
            using (var process = Process.Start(info))
            {
                if (process == null) throw new IOException("模板组件未启动。");
                var timer = Stopwatch.StartNew();
                long lastPoll = -1;
                while (!process.WaitForExit(200))
                {
                    Application.DoEvents();
                    if ((long)timer.Elapsed.TotalSeconds != lastPoll)
                    {
                        lastPoll = (long)timer.Elapsed.TotalSeconds;
                        string progressPath = Path.Combine(Path.GetDirectoryName(manifest), "import-progress.json");
                        try {
                            if (File.Exists(progressPath)) {
                                var progress = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(progressPath));
                                status.Text = progress["message"] + " · " + progress["progress"] + "% · 已等待 " + lastPoll + " 秒";
                            }
                        } catch (IOException) { /* Atomic progress replacement can race a read; the next bounded poll retries. */ }
                    }
                    if (timer.Elapsed.TotalSeconds > 3700)
                        throw new TimeoutException("模板结果等待超时；操作及实例保留，不会终止或重放MuMu导入。请在平台核对。");
                }
                if (process.ExitCode != 0)
                {
                    string message = "模板导入未通过，旧默认保留。请查看平台模板操作及诊断结果。";
                    if (File.Exists(result)) {
                        var record = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(result));
                        if (record.ContainsKey("message")) message += " " + record["message"];
                    }
                    throw new InvalidOperationException(message);
                }
                if (!File.Exists(result)) throw new IOException("缺少模板验收回执，不能宣称成功。");
            }
        }
    }
}
