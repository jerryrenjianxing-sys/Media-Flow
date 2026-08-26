using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;

internal static class RiskFlowLauncher
{
    [STAThread]
    private static void Main(string[] args)
    {
        string projectRoot = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(
            Path.DirectorySeparatorChar,
            Path.AltDirectorySeparatorChar);
        string scriptPath = Path.Combine(projectRoot, "run-riskflow-console.ps1");

        if (!File.Exists(scriptPath))
        {
            MessageBox.Show(
                "没有找到 RiskFlow 启动文件。请把本程序放在 RiskFlow 项目根目录后再打开。",
                "RiskFlow",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
            return;
        }

        bool noBrowser = Array.Exists(
            args,
            value => string.Equals(value, "--no-browser", StringComparison.OrdinalIgnoreCase));

        string powerShellArguments = "-NoProfile -ExecutionPolicy Bypass -File \"" +
            scriptPath + "\"" + (noBrowser ? " -NoBrowser" : string.Empty);

        var startInfo = new ProcessStartInfo
        {
            FileName = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.Windows),
                "System32",
                "WindowsPowerShell",
                "v1.0",
                "powershell.exe"),
            Arguments = powerShellArguments,
            WorkingDirectory = projectRoot,
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden,
            RedirectStandardError = true,
            RedirectStandardOutput = true,
            StandardErrorEncoding = Encoding.UTF8,
            StandardOutputEncoding = Encoding.UTF8
        };

        try
        {
            using (Process process = Process.Start(startInfo))
            {
                string standardOutput = process.StandardOutput.ReadToEnd();
                string standardError = process.StandardError.ReadToEnd();
                process.WaitForExit();

                if (process.ExitCode != 0)
                {
                    string details = string.IsNullOrWhiteSpace(standardError)
                        ? standardOutput
                        : standardError;
                    MessageBox.Show(
                        "RiskFlow 启动失败。\n\n" + details.Trim(),
                        "RiskFlow",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Error);
                }
            }
        }
        catch (Exception exception)
        {
            MessageBox.Show(
                "RiskFlow 启动失败。\n\n" + exception.Message,
                "RiskFlow",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
        }
    }
}
