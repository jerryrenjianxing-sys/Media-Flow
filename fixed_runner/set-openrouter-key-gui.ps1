$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$projectRoot = Split-Path $PSScriptRoot -Parent
$savedKeyPath = Join-Path $projectRoot '.secrets\openrouter-api-key.dpapi'
$secretDirectory = Split-Path -Parent $savedKeyPath

$form = New-Object System.Windows.Forms.Form
$form.Text = 'MediaFlow - 保存 OpenRouter Key'
$form.StartPosition = 'CenterScreen'
$form.ClientSize = New-Object System.Drawing.Size(520, 210)
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.TopMost = $true

$title = New-Object System.Windows.Forms.Label
$title.Text = '粘贴 OpenRouter API Key'
$title.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', 13, [System.Drawing.FontStyle]::Bold)
$title.Location = New-Object System.Drawing.Point(24, 20)
$title.AutoSize = $true
$form.Controls.Add($title)

$hint = New-Object System.Windows.Forms.Label
$hint.Text = '输入内容不会显示；保存后仅以当前 Windows 用户可解密的形式存放。'
$hint.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', 9)
$hint.ForeColor = [System.Drawing.Color]::DimGray
$hint.Location = New-Object System.Drawing.Point(26, 56)
$hint.AutoSize = $true
$form.Controls.Add($hint)

$keyBox = New-Object System.Windows.Forms.TextBox
$keyBox.Location = New-Object System.Drawing.Point(28, 88)
$keyBox.Size = New-Object System.Drawing.Size(464, 28)
$keyBox.UseSystemPasswordChar = $true
$keyBox.Font = New-Object System.Drawing.Font('Segoe UI', 10)
$form.Controls.Add($keyBox)

$status = New-Object System.Windows.Forms.Label
$status.Location = New-Object System.Drawing.Point(28, 128)
$status.Size = New-Object System.Drawing.Size(300, 28)
$status.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', 9)
$form.Controls.Add($status)

$saveButton = New-Object System.Windows.Forms.Button
$saveButton.Text = '安全保存'
$saveButton.Location = New-Object System.Drawing.Point(376, 136)
$saveButton.Size = New-Object System.Drawing.Size(116, 38)
$saveButton.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', 9, [System.Drawing.FontStyle]::Bold)
$form.Controls.Add($saveButton)
$form.AcceptButton = $saveButton

$saveButton.Add_Click({
    if ([string]::IsNullOrWhiteSpace($keyBox.Text)) {
        $status.Text = '请先粘贴 Key。'
        $status.ForeColor = [System.Drawing.Color]::Firebrick
        return
    }
    try {
        New-Item -ItemType Directory -Force -Path $secretDirectory | Out-Null
        $secureKey = ConvertTo-SecureString $keyBox.Text -AsPlainText -Force
        $encryptedKey = ConvertFrom-SecureString -SecureString $secureKey
        [IO.File]::WriteAllText($savedKeyPath, $encryptedKey, [Text.UTF8Encoding]::new($false))
        $keyBox.Clear()
        $status.Text = '保存成功，可以关闭窗口。'
        $status.ForeColor = [System.Drawing.Color]::DarkGreen
        $saveButton.Enabled = $false
        $form.DialogResult = [System.Windows.Forms.DialogResult]::OK
        $form.Close()
    }
    catch {
        $keyBox.Clear()
        $status.Text = '保存失败，请关闭后重试。'
        $status.ForeColor = [System.Drawing.Color]::Firebrick
    }
})

$form.Add_Shown({ $keyBox.Focus() })
[void]$form.ShowDialog()
