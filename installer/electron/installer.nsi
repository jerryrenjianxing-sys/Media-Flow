; One release executable supports production and an explicitly isolated profile.
; No process-name matching or forced process termination is used.
!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"
!include "StdUtils.nsh"
!include "extractAppPackage.nsh"
RequestExecutionLevel user
Unicode true
Name "MediaFlow ${VERSION}"
BrandingText "MediaFlow"
ShowInstDetails show
ShowUninstDetails show
InstallDir "$LOCALAPPDATA\Programs\MediaFlow"
!define APP_EXECUTABLE_FILENAME "MediaFlow.exe"
!define UNINSTALL_FILENAME "Uninstall MediaFlow.exe"
Var ProfilePath
Var LegacyInstallerPath
Var TargetRoot
Var CandidateRoot
Var ResultCode
Var ResultText
Var IsUpdate
Var InstallationLog

!macro ExtractHelpers
  InitPluginsDir
  SetOutPath "$PLUGINSDIR"
  File /oname=installer-helper.ps1 "${BUILD_RESOURCES_DIR}\installer-helper.ps1"
  File /oname=instance-profile.ps1 "${BUILD_RESOURCES_DIR}\instance-profile.ps1"
!macroend
!macro ResolveInstance
  ${GetParameters} $R0
  ${GetOptions} $R0 "/INSTANCEPROFILE=" $ProfilePath
  ${GetOptions} $R0 "/LEGACYINSTALLER=" $LegacyInstallerPath
  nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\installer-helper.ps1" -Action Resolve -InstallRoot "$INSTDIR" -InstanceProfilePath "$ProfilePath" -IniPath "$PLUGINSDIR\instance.ini"'
  Pop $ResultCode
  Pop $ResultText
  ${If} $ResultCode != 0
    ${If} ${Silent}
      SetErrorLevel 1
      Quit
    ${EndIf}
    MessageBox MB_OK|MB_ICONSTOP "无法识别此 MediaFlow 安装实例。$\r$\n$ResultText"
    SetErrorLevel 1
    Quit
  ${EndIf}
  ReadINIStr $INSTDIR "$PLUGINSDIR\instance.ini" "instance" "install_root"
  ReadINIStr $ProfilePath "$PLUGINSDIR\instance.ini" "instance" "profile"
  StrCpy $InstallationLog "$INSTDIR.installation.log"
!macroend
!macro RequireHelperSuccess DESCRIPTION
  Pop $ResultCode
  Pop $ResultText
  DetailPrint "$ResultText"
  ${IfNot} ${FileExists} "$InstallationLog"
    FileOpen $R9 "$InstallationLog" w
    FileWriteUTF16LE /BOM $R9 "MediaFlow installer diagnostic$\r$\n"
    FileClose $R9
  ${EndIf}
  FileOpen $R9 "$InstallationLog" a
  FileSeek $R9 0 END
  ${If} $ResultCode == 0
    FileWriteUTF16LE $R9 "安装阶段完成。 code=0$\r$\n$ResultText$\r$\n"
  ${Else}
    FileWriteUTF16LE $R9 "${DESCRIPTION} code=$ResultCode$\r$\n$ResultText$\r$\n"
  ${EndIf}
  FileClose $R9
  ${If} $ResultCode != 0
    ${If} ${Silent}
      SetErrorLevel 1
      Quit
    ${EndIf}
    MessageBox MB_OK|MB_ICONSTOP "${DESCRIPTION}$\r$\n$ResultText$\r$\n已保留旧程序回退资料，用户数据保持原位置。"
    SetErrorLevel 1
    Quit
  ${EndIf}
!macroend

  Function un.onInit
    StrCpy $LANGUAGE 2052
    SetRegView 64
    !insertmacro ExtractHelpers
    !insertmacro ResolveInstance
  FunctionEnd
  !insertmacro MUI_UNPAGE_WELCOME
  !insertmacro MUI_UNPAGE_INSTFILES
  !insertmacro MUI_UNPAGE_FINISH
  Section "Uninstall"
    nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\installer-helper.ps1" -Action Remove -InstallRoot "$INSTDIR" -InstanceProfilePath "$ProfilePath"'
    !insertmacro RequireHelperSuccess "无法安全停止并注销此实例的后台。"
    ; The helper verified the exact program root and preserved external data.
    RMDir /r "$INSTDIR"
    ${If} ${FileExists} "$INSTDIR\MediaFlow.exe"
      MessageBox MB_OK|MB_ICONSTOP "程序文件未完全移除，用户数据保持原位置。"
      SetErrorLevel 1
    ${EndIf}
  SectionEnd
  Function .onInit
    StrCpy $LANGUAGE 2052
    ${IfNot} ${RunningX64}
      MessageBox MB_OK|MB_ICONSTOP "MediaFlow 需要 Windows x64 系统。"
      SetErrorLevel 1
      Quit
    ${EndIf}
    SetRegView 64
    !insertmacro ExtractHelpers
    ; /D is interpreted by NSIS; profile install_root is authoritative for isolation.
    !insertmacro ResolveInstance
    ${GetParameters} $R0
    ${GetOptions} $R0 "--updated" $R1
    StrCpy $IsUpdate "false"
    ${IfNot} ${Errors}
      StrCpy $IsUpdate "true"
    ${EndIf}
  FunctionEnd
  Function SkipInitialPageForUpdate
    ${If} $IsUpdate == "true"
      Abort
    ${EndIf}
  FunctionEnd
  !define MUI_PAGE_CUSTOMFUNCTION_PRE SkipInitialPageForUpdate
  !insertmacro MUI_PAGE_WELCOME
  !define MUI_PAGE_CUSTOMFUNCTION_PRE SkipInitialPageForUpdate
  !insertmacro MUI_PAGE_LICENSE "${BUILD_RESOURCES_DIR}\..\..\LICENSE"
  !define MUI_PAGE_CUSTOMFUNCTION_PRE SkipInitialPageForUpdate
  !insertmacro MUI_PAGE_DIRECTORY
  !insertmacro MUI_PAGE_INSTFILES
  Function OpenMediaFlow
    Exec '"$INSTDIR\MediaFlow.exe" --instance-profile="$ProfilePath"'
  FunctionEnd
  !define MUI_FINISHPAGE_RUN
  !define MUI_FINISHPAGE_RUN_FUNCTION OpenMediaFlow
  !insertmacro MUI_PAGE_FINISH
  Section "Install"
    StrCpy $TargetRoot "$INSTDIR"
    System::Call 'kernel32::GetTickCount()i.r0'
    StrCpy $CandidateRoot "$TargetRoot.candidate-$0"
    StrCpy $INSTDIR "$CandidateRoot"
    SetOutPath "$INSTDIR"
    !insertmacro extractEmbeddedAppPackage
    WriteUninstaller "$INSTDIR\Uninstall MediaFlow.exe"
    StrCpy $INSTDIR "$TargetRoot"
    ; Release NSIS's working directory before the candidate is moved into place.
    SetOutPath "$PLUGINSDIR"
    DetailPrint "正在核验实例、保存回退资料并等待后台安全退出…"
    nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\installer-helper.ps1" -Action Prepare -InstallRoot "$TargetRoot" -CandidateRoot "$CandidateRoot" -InstanceProfilePath "$ProfilePath" -LegacyInstallerPath "$LegacyInstallerPath" -InstallerPath "$EXEPATH" -StatePath "$PLUGINSDIR\transaction.json"'
    !insertmacro RequireHelperSuccess "安装准备未完成，未继续替换程序。"
    nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\installer-helper.ps1" -Action Commit -InstallRoot "$TargetRoot" -InstanceProfilePath "$ProfilePath" -InstallerPath "$EXEPATH" -Version "${VERSION}" -StatePath "$PLUGINSDIR\transaction.json"'
    !insertmacro RequireHelperSuccess "新版程序登记失败。"
    ; Resolve the newly created production marker through UTF-8 PowerShell and UTF-16 INI.
    !insertmacro ResolveInstance
    ${GetParameters} $R0
    ${GetOptions} $R0 "--force-run" $R1
    ${IfNot} ${Errors}
      Call OpenMediaFlow
    ${EndIf}
    SetErrorLevel 0
    ${If} $IsUpdate == "true"
      Quit
    ${EndIf}
  SectionEnd
!insertmacro addLangs
