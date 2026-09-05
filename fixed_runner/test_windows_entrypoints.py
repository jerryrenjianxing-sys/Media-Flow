from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WINDOWS_POWERSHELL = (
    Path(os.environ.get("WINDIR", r"C:\Windows"))
    / "System32"
    / "WindowsPowerShell"
    / "v1.0"
    / "powershell.exe"
)


@unittest.skipUnless(os.name == "nt" and WINDOWS_POWERSHELL.is_file(), "Windows only")
class WindowsEntrypointTests(unittest.TestCase):
    def test_desktop_shell_declares_per_monitor_v2_dpi_awareness(self) -> None:
        manifest_path = PROJECT_ROOT / "launcher" / "MediaFlow.exe.manifest"
        self.assertTrue(
            manifest_path.is_file(),
            "desktop shell must ship an application manifest instead of relying on Windows bitmap scaling",
        )
        manifest = manifest_path.read_text(encoding="utf-8")
        self.assertIn("PerMonitorV2,PerMonitor", manifest)
        self.assertIn("true/pm", manifest)

        build_script = (PROJECT_ROOT / "launcher" / "build-launcher.ps1").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("/win32manifest:$manifest", build_script)

    def test_runtime_entrypoints_parse_in_windows_powershell_51(self) -> None:
        for relative_path in (
            "install-mediaflow-background.ps1",
            "manage-mediaflow.ps1",
            "run-mediaflow-console.ps1",
            "uninstall-mediaflow-background.ps1",
            "install-riskflow-background.ps1",
            "manage-riskflow.ps1",
            "run-riskflow-console.ps1",
            "uninstall-riskflow-background.ps1",
        ):
            path = PROJECT_ROOT / relative_path
            escaped_path = str(path).replace("'", "''")
            parser = (
                "$tokens=$null;$errors=$null;"
                "[System.Management.Automation.Language.Parser]::ParseFile("
                f"'{escaped_path}',[ref]$tokens,[ref]$errors)|Out-Null;"
                "if($errors.Count){"
                "$errors|ForEach-Object{[pscustomobject]@{Message=$_.Message;"
                "Line=$_.Extent.StartLineNumber}}|ConvertTo-Json -Compress;exit 1}"
            )
            completed = subprocess.run(
                [
                    str(WINDOWS_POWERSHELL),
                    "-NoProfile",
                    "-Command",
                    parser,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            )
            detail = completed.stdout.strip() or completed.stderr.strip()
            self.assertEqual(
                completed.returncode,
                0,
                f"{relative_path} is not Windows PowerShell 5.1 compatible: {detail}",
            )

    def test_runtime_entrypoints_are_ascii_safe(self) -> None:
        for relative_path in (
            "install-mediaflow-background.ps1",
            "manage-mediaflow.ps1",
            "run-mediaflow-console.ps1",
            "uninstall-mediaflow-background.ps1",
            "install-riskflow-background.ps1",
            "manage-riskflow.ps1",
            "run-riskflow-console.ps1",
            "uninstall-riskflow-background.ps1",
        ):
            data = (PROJECT_ROOT / relative_path).read_bytes()
            self.assertTrue(
                data.startswith(b"\xef\xbb\xbf") or data.isascii(),
                f"{relative_path} must use a UTF-8 BOM or remain ASCII-only",
            )

    def test_installer_appends_mediaflow_when_drive_root_is_selected(self) -> None:
        source = (PROJECT_ROOT / "installer" / "MediaFlowInstaller.cs").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("NormalizeInstallPath", source)
        self.assertIn('Path.Combine(full, "MediaFlow")', source)
        self.assertIn("installPath.Text = NormalizeInstallPath", source)

    def test_installer_verifies_background_registration_before_success(self) -> None:
        source = (PROJECT_ROOT / "installer" / "MediaFlowInstaller.cs").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("VerifyInstalledState", source)
        self.assertIn("MediaFlow Background", source)
        self.assertIn("--veloapp-install", source)

    def test_installer_stops_old_background_before_overwrite_and_restores_on_failure(self) -> None:
        source = (PROJECT_ROOT / "installer" / "MediaFlowInstaller.cs").read_text(
            encoding="utf-8-sig"
        )
        prepare = source.index("PrepareExistingInstallation(normalized")
        launch = source.index("Process.Start(info)")
        self.assertLess(prepare, launch)
        self.assertIn('" -Action Stop -NoBrowser"', source)
        self.assertIn("uninstall-mediaflow-background.ps1", source)
        self.assertIn("TryRestoreExistingBackground", source)

    def test_installer_supports_same_version_repair_install(self) -> None:
        source = (PROJECT_ROOT / "installer" / "MediaFlowInstaller.cs").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("PrepareSameVersionRepair", source)
        self.assertIn('"uninstall --silent"', source)
        self.assertIn("同版本修复安装", source)
        self.assertIn("ReadBundledSourceRevision", source)
        self.assertIn("相同版本号但代码身份不同", source)

    def test_installer_compares_numbered_prerelease_versions(self) -> None:
        source = (PROJECT_ROOT / "installer" / "MediaFlowInstaller.cs").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("ReadBundledVersion", source)
        self.assertIn("CompareSemanticVersions", source)
        self.assertIn("prerelease", source)

    def test_installer_child_processes_have_bounded_waits(self) -> None:
        source = (PROJECT_ROOT / "installer" / "MediaFlowInstaller.cs").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("WaitForExitWithUi", source)
        self.assertIn("安装程序等待超时", source)
        self.assertIn("命令执行超时", source)

    def test_release_version_has_one_canonical_source(self) -> None:
        version_file = PROJECT_ROOT / "packaging" / "version.json"
        payload = json.loads(version_file.read_text(encoding="utf-8-sig"))
        version = payload["version"]
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")
        self.assertEqual(version, "0.4.1")
        self.assertEqual(payload["development_iteration"], 10)
        installer = (PROJECT_ROOT / "installer" / "MediaFlowInstaller.cs").read_text(
            encoding="utf-8-sig"
        )
        launcher = (PROJECT_ROOT / "launcher" / "RiskFlowLauncher.cs").read_text(
            encoding="utf-8-sig"
        )
        assembly_version = version + ".0"
        self.assertIn(f'AssemblyFileVersion("{assembly_version}")', installer)
        self.assertIn(f'AssemblyFileVersion("{assembly_version}")', launcher)
        build = (PROJECT_ROOT / "packaging" / "build-windows-release.ps1").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("packaging\\version.json", build)
        self.assertIn("must match canonical version", build)
        self.assertIn("Refusing to create an installer from a dirty working tree", build)
        self.assertIn("git -c core.excludesfile=", build)
        self.assertIn('"$canonicalVersion-dev.$developmentIteration"', build)
        self.assertIn('"$packageVersion+$($sourceRevision.Trim())', build)
        self.assertIn('--packVersion $packageVersion', build)
        self.assertIn('$releaseTag = "mediaflow/v$packageVersion"', build)
        self.assertIn('must point to the exact source revision', build)
        self.assertIn("[ValidateSet('development', 'release')]", build)
        self.assertIn("[string]$Channel = 'development'", build)
        self.assertIn("$developmentBuild = $Channel -eq 'development'", build)
        self.assertIn("A release-channel build must come from one clean committed source revision", build)
        self.assertIn("distribution_label = if ($developmentBuild) { 'development-stage' }", build)
        self.assertIn("development_iteration = if ($developmentBuild)", build)
        self.assertIn("target_version = $Version", build)
        self.assertIn("Get-FileHash -LiteralPath $installerPath -Algorithm SHA256", build)
        self.assertIn("MediaFlow-Installer.exe.sha256", build)
        self.assertIn("[System.IO.File]::WriteAllText", build)

        installer_build = (PROJECT_ROOT / "installer" / "build-installer.ps1").read_text(
            encoding="utf-8-sig"
        )
        launcher_build = (PROJECT_ROOT / "launcher" / "build-launcher.ps1").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("AssemblyInformationalVersion", installer_build)
        self.assertIn("AssemblyInformationalVersion", launcher_build)
        self.assertIn("AssemblyMetadata", installer_build)
        self.assertIn("AssemblyMetadata", launcher_build)

    def test_release_build_never_restarts_the_running_local_ui(self) -> None:
        build = (PROJECT_ROOT / "packaging" / "build-windows-release.ps1").read_text(
            encoding="utf-8-sig"
        )
        self.assertNotIn("runtime_control.py') refresh-ui", build)
        self.assertIn("Packaging is deliberately side-effect free", build)
        self.assertIn("running developer or installed UI is never restarted", build)

    def test_release_entrypoints_do_not_default_to_a_development_device(self) -> None:
        for relative_path in (
            "fixed_runner/worker.py",
            "fixed_runner/douyin_fixed_runner.py",
            "fixed_runner/douyin_uia2_runner.py",
        ):
            source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8-sig")
            self.assertNotIn("P7HUDEKF4XVODY4D", source)
        build = (PROJECT_ROOT / "packaging" / "build-windows-release.ps1").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("P7HUDEKF4XVODY4D", build)

    def test_desktop_shell_offers_confirmed_uninstall(self) -> None:
        source = (PROJECT_ROOT / "launcher" / "RiskFlowLauncher.cs").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn('menu.Items.Add("卸载 MediaFlow…"', source)
        self.assertIn("ConfirmAndUninstall", source)
        self.assertIn('Arguments = "uninstall --silent"', source)

    def test_desktop_shell_forces_utf8_for_powershell_output(self) -> None:
        source = (PROJECT_ROOT / "launcher" / "RiskFlowLauncher.cs").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("[Console]::OutputEncoding", source)
        self.assertIn("-EncodedCommand", source)

    def test_desktop_window_appears_before_bounded_background_startup(self) -> None:
        source = (PROJECT_ROOT / "launcher" / "RiskFlowLauncher.cs").read_text(
            encoding="utf-8-sig"
        )
        main = source[source.index("private static int Main"):source.index("private static bool HasArgument")]
        self.assertIn("new MediaFlowForm(projectRoot, dataRoot, pageUrl, activate, scriptPath)", main)
        self.assertIn("StartBackgroundAndInitialize", source)
        self.assertIn("WaitForExit(timeoutMilliseconds)", source)
        self.assertIn("后台启动超过60秒", source)
        self.assertIn("打开诊断目录", source)
        self.assertIn("查看诊断", source)


if __name__ == "__main__":
    unittest.main()
