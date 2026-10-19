param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $MediaFlowArguments
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
# Escaped JSON keeps Windows PowerShell 5 compatible with UTF-8 without a BOM.
$messages = '{"invalid_config":"Skill \u914d\u7f6e\u65e0\u6548\uff1b\u8bf7\u68c0\u67e5\u539f\u914d\u7f6e\uff0c\u4e0d\u8981\u8986\u76d6\u6216\u91cd\u65b0\u521b\u5efa\u6574\u5957\u6587\u4ef6\u3002","missing_connection_file":"\u8fde\u63a5\u8d44\u6599\u6587\u4ef6\u4e0d\u5b58\u5728\u3002\u8bf7\u91cd\u65b0\u590d\u5236\u8fde\u63a5\u8bf4\u660e\u6216\u6253\u5f00\u5df2\u5b89\u88c5\u7684 MediaFlow\uff1b\u4e0d\u8981\u641c\u7d22\u5168\u76d8\u6216\u731c\u6d4b\u8def\u5f84\u3002","invalid_connection_file":"\u8fde\u63a5\u8d44\u6599\u635f\u574f\u6216\u4e0d\u5b8c\u6574\u3002\u8bf7\u91cd\u65b0\u590d\u5236\u8fde\u63a5\u8bf4\u660e\u6216\u6253\u5f00\u5df2\u5b89\u88c5\u7684 MediaFlow\uff1b\u6ca1\u6709\u542f\u52a8\u670d\u52a1\u6216\u4efb\u52a1\u3002","stale_connection":"\u8fde\u63a5\u8d44\u6599\u5df2\u8fc7\u671f\uff0c\u7a0b\u5e8f\u6216 Skill \u8def\u5f84\u5df2\u5931\u6548\u3002\u8bf7\u91cd\u65b0\u590d\u5236\u8fde\u63a5\u8bf4\u660e\u6216\u6253\u5f00\u5df2\u5b89\u88c5\u7684 MediaFlow\uff0c\u4e0d\u8981\u91cd\u5efa\u811a\u672c\u3002","wrong_instance":"\u8fde\u63a5\u5230\u4e86\u4e0d\u540c\u7684 MediaFlow \u5b9e\u4f8b\uff0c\u672a\u53d1\u9001\u4e1a\u52a1\u64cd\u4f5c\u3002\u8bf7\u91cd\u65b0\u590d\u5236\u5f53\u524d\u8f6f\u4ef6\u7684\u8fde\u63a5\u8bf4\u660e\u3002","unexpected_service":"\u8be5\u5730\u5740\u4e0d\u662f\u652f\u6301\u7684 MediaFlow \u63a5\u53e3\u3002\u8bf7\u6838\u5bf9\u8fde\u63a5\u8bf4\u660e\u6216\u6253\u5f00\u5df2\u5b89\u88c5\u7684\u8f6f\u4ef6\uff0c\u4e0d\u8981\u91cd\u590d\u542f\u52a8\u540e\u53f0\u3002","invalid_address":"\u8fde\u63a5\u5730\u5740\u5fc5\u987b\u662f\u672c\u673a\u56de\u73af HTTP \u5730\u5740\uff1b\u8bf7\u91cd\u65b0\u590d\u5236\u5f53\u524d\u8f6f\u4ef6\u7684\u8fde\u63a5\u8bf4\u660e\u3002","connection_failed":"\u6682\u65f6\u65e0\u6cd5\u8fde\u63a5 MediaFlow\uff0c\u5c1a\u4e0d\u80fd\u786e\u5b9a\u670d\u52a1\u672a\u542f\u52a8\u3002\u8bf7\u5148\u8be2\u95ee\u7528\u6237\u662f\u5426\u6253\u5f00\u6216\u68c0\u67e5\u8f6f\u4ef6\uff1b\u6ca1\u6709\u542f\u52a8\u4efb\u4f55\u670d\u52a1\u6216\u4efb\u52a1\u3002","connection_timeout":"\u8fde\u63a5\u68c0\u67e5\u57285\u79d2\u5185\u672a\u5b8c\u6210\uff0c\u5c1a\u4e0d\u80fd\u786e\u5b9a\u670d\u52a1\u672a\u542f\u52a8\u3002\u8bf7\u5148\u8be2\u95ee\u7528\u6237\u518d\u5904\u7406\uff1b\u6ca1\u6709\u542f\u52a8\u4efb\u4f55\u670d\u52a1\u6216\u4efb\u52a1\u3002","http_error":"\u5e73\u53f0\u63a5\u53e3\u8fd4\u56de\u9519\u8bef\uff1b\u8bf7\u68c0\u67e5\u5df2\u5b89\u88c5\u7684\u8f6f\u4ef6\u6216\u91cd\u65b0\u590d\u5236\u8fde\u63a5\u8bf4\u660e\uff0c\u4e0d\u8981\u91cd\u590d\u542f\u52a8\u670d\u52a1\u3002","connected":"\u5df2\u8fde\u63a5 MediaFlow\u3002\u7b80\u8981\u8bf4\u660e\u8fde\u63a5\u7ed3\u679c\uff0c\u5e76\u6309\u7528\u6237\u76ee\u6807\u7ee7\u7eed\uff1b\u6ca1\u6709\u5177\u4f53\u76ee\u6807\u65f6\u7ed9\u51fa2\u81f33\u4e2a\u53ef\u4ea4\u529e\u7684\u4f8b\u5b50\u3002\u8bbe\u5907\u662f\u5426\u5728\u7ebf\u548c\u4efb\u52a1\u662f\u5426\u5c31\u7eea\u5c1a\u672a\u68c0\u67e5\u3002","runtime_missing":"\u5e73\u53f0\u5df2\u8fde\u63a5\uff0c\u4f46\u5176\u5ba2\u6237\u7aef\u8fd0\u884c\u73af\u5883\u4e0d\u53ef\u7528\u3002\u53ea\u8bfb\u8fde\u63a5\u5df2\u5b8c\u6210\uff1b\u9700\u8981\u4e1a\u52a1\u64cd\u4f5c\u65f6\u8bf7\u4fee\u590d\u6216\u66f4\u65b0\u5df2\u5b89\u88c5\u8f6f\u4ef6\uff0c\u4e0d\u8981\u53e6\u88c5\u5f00\u53d1\u5de5\u5177\u3002","runtime_failed":"\u5e73\u53f0\u81ea\u5e26\u8fd0\u884c\u73af\u5883\u672a\u80fd\u6267\u884c\u5ba2\u6237\u7aef\u3002\u8bf7\u68c0\u67e5\u5df2\u5b89\u88c5\u8f6f\u4ef6\uff1b\u672a\u786e\u8ba4\u4e1a\u52a1\u7ed3\u679c\u65f6\u67e5\u8be2\u539f\u56de\u6267\uff0c\u4e0d\u8981\u91cd\u53d1\u5199\u5165\u3002"}' | ConvertFrom-Json
$checking = $MediaFlowArguments.Count -gt 0 -and $MediaFlowArguments[0] -eq 'check'
function Finish-Failure([string] $Code) {
    $next = if ($Code -in @('missing_connection_file', 'invalid_connection_file', 'stale_connection', 'wrong_instance')) { 'refresh_connection' } else { 'ask_user' }
    $service = if ($Code -eq 'runtime_missing') { 'running' } else { 'unknown' }
    [ordered]@{ ok=$false; reason_code=$Code; service_state=$service;
        next_action=$next; user_message=$messages.$Code } | ConvertTo-Json -Depth 8 -Compress
    exit 2
}
function Same-Path([string] $Left, [string] $Right) {
    if (-not $Left -or -not $Right) { return $false }
    try {
        return [string]::Equals([IO.Path]::GetFullPath($Left).TrimEnd('\', '/'),
            [IO.Path]::GetFullPath($Right).TrimEnd('\', '/'), [StringComparison]::OrdinalIgnoreCase)
    } catch { return $false }
}
function Get-AutomationUri([string] $Address) {
    $uri = [Uri]$Address
    $ip = $null
    $local = $uri.Host -eq 'localhost' -or ([Net.IPAddress]::TryParse($uri.DnsSafeHost, [ref]$ip) -and [Net.IPAddress]::IsLoopback($ip))
    if (-not $uri.IsAbsoluteUri -or -not $local -or $uri.Scheme -ne 'http' -or $uri.UserInfo -or $uri.Query -or $uri.Fragment -or $uri.AbsolutePath -notin @('', '/', '/api/automation')) { throw 'Invalid loopback address' }
    $builder = [UriBuilder]$uri
    if ($builder.Host -eq 'localhost') { $builder.Host = '127.0.0.1' }
    $builder.Path = '/api/automation'
    return $builder.Uri
}

$configPath = if ($env:MEDIAFLOW_SKILL_CONFIG) { $env:MEDIAFLOW_SKILL_CONFIG } else {
    Join-Path (Split-Path -Parent $PSScriptRoot) 'config.json'
}
$connectionFile = $null
for ($index = 0; $index -lt $MediaFlowArguments.Count; $index++) {
    $argument = $MediaFlowArguments[$index]
    if ($argument -in @('--config', '--connection-file')) {
        if ($index + 1 -ge $MediaFlowArguments.Count) { Finish-Failure 'invalid_config' }
        $index++
        if ($argument -eq '--config') { $configPath = $MediaFlowArguments[$index] } else {
            if ($connectionFile) { Finish-Failure 'invalid_connection_file' }
            $connectionFile = $MediaFlowArguments[$index]
        }
    } elseif ($argument.StartsWith('--config=')) { $configPath = $argument.Substring(9)
    } elseif ($argument.StartsWith('--connection-file=')) {
        if ($connectionFile) { Finish-Failure 'invalid_connection_file' }
        $connectionFile = $argument.Substring(18)
        if (-not $connectionFile) { Finish-Failure 'invalid_connection_file' }
    }
}
$config = @{}
$connection = $null
$runtime = $env:MEDIAFLOW_PYTHON
if ($connectionFile) {
    if (-not (Test-Path -LiteralPath $connectionFile -PathType Leaf)) { Finish-Failure 'missing_connection_file' }
    try {
        $connection = Get-Content -LiteralPath $connectionFile -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($connection.schema -ne 1 -or $connection.product -ne 'MediaFlow') { throw 'Unsupported connection' }
        foreach ($key in @('instance_id', 'data_root', 'app_root', 'api_url', 'skill_path', 'client_path', 'python')) {
            if (-not ($connection.$key -is [string]) -or [string]::IsNullOrWhiteSpace($connection.$key)) { throw 'Incomplete connection' }
        }
        foreach ($key in @('data_root', 'app_root', 'skill_path', 'client_path', 'python')) {
            if (-not [IO.Path]::IsPathRooted($connection.$key)) { throw 'Relative path' }
        }
    } catch { Finish-Failure 'invalid_connection_file' }
    if (-not (Test-Path -LiteralPath $connection.app_root -PathType Container) -or
        -not (Test-Path -LiteralPath $connection.skill_path -PathType Leaf) -or
        -not (Test-Path -LiteralPath $connection.client_path -PathType Leaf)) { Finish-Failure 'stale_connection' }
    $runtime = $connection.python
} elseif (Test-Path -LiteralPath $configPath -PathType Leaf) {
    try {
        $config = Get-Content -Raw -LiteralPath $configPath -Encoding UTF8 | ConvertFrom-Json
        if (-not $runtime) { $runtime = $config.python }
    } catch { Finish-Failure 'invalid_config' }
}

if ($checking -or $connection -or -not $runtime) {
    $failureCode = 'connection_failed'
    try {
        $api = if ($connection) { $connection.api_url } elseif ($env:MEDIAFLOW_API_URL) {
            $env:MEDIAFLOW_API_URL
        } elseif ($config.api_url) { $config.api_url } else { 'http://127.0.0.1:48138' }
        try { $uri = Get-AutomationUri $api } catch { Finish-Failure 'invalid_address' }
        $request = [Net.HttpWebRequest]::Create($uri)
        $request.Proxy = $null
        $request.AllowAutoRedirect = $false
        $request.Timeout = 5000
        $request.ReadWriteTimeout = 5000
        $clock = [Diagnostics.Stopwatch]::StartNew()
        $response = $request.GetResponse()
        try {
            $failureCode = 'unexpected_service'
            if ([int]$response.StatusCode -ne 200) { throw 'Platform discovery failed' }
            $reader = New-Object IO.StreamReader($response.GetResponseStream())
            try {
                # Headers and the streamed body share one five-second deadline.
                $buffer = New-Object char[] 4096
                $text = New-Object Text.StringBuilder
                while ($true) {
                    $reading = $reader.ReadAsync($buffer, 0, $buffer.Length)
                    $remaining = [Math]::Max(0, 5000 - [int]$clock.ElapsedMilliseconds)
                    if ($remaining -le 0 -or -not $reading.Wait($remaining)) {
                        $failureCode = 'connection_timeout'
                        $request.Abort()
                        throw 'Discovery timed out'
                    }
                    $count = $reading.Result
                    if ($count -eq 0) { break }
                    if ($text.Length + $count -gt 1048576) { $request.Abort(); throw 'Response too large' }
                    [void]$text.Append($buffer, 0, $count)
                }
                $info = $text.ToString() | ConvertFrom-Json
            } finally { $reader.Dispose() }
        } finally { $response.Dispose() }
        if ($info.product -ne 'MediaFlow' -or $info.api_version -ne '1' -or $info.status -ne 'available') { Finish-Failure 'unexpected_service' }
        if ($connection) {
            $actual = $info.connection
            if (-not $actual -or $actual.schema -ne 1 -or $actual.product -ne 'MediaFlow' -or
                $actual.instance_id -cne $connection.instance_id -or
                -not (Same-Path $actual.data_root $connection.data_root)) { Finish-Failure 'wrong_instance' }
            if (-not (Same-Path $actual.app_root $connection.app_root) -or
                -not (Same-Path $actual.skill_path $connection.skill_path) -or
                -not (Same-Path $actual.client_path $connection.client_path) -or
                -not (Same-Path $actual.python $connection.python)) { Finish-Failure 'stale_connection' }
            foreach ($key in @('version', 'source_revision', 'build_identity')) {
                if ($connection.version.$key -and $actual.version.$key -cne $connection.version.$key) { Finish-Failure 'stale_connection' }
            }
        }
        $runtimeInfo = if ($connection) {
            $available = [bool]($info.connection.runtime_available -and (Test-Path -LiteralPath $runtime -PathType Leaf))
            [ordered]@{ python=$runtime; available=$available;
                reason_code=$(if ($available) { 'ready' } else { 'runtime_missing' }) }
        } else { $info.client_runtime }
        if ($checking) {
            $runtimeReady = [bool]$runtimeInfo.available
            [ordered]@{ ok=$true; reason_code='connected'; service_state='running';
                api_url=$uri.GetLeftPart([UriPartial]::Authority); product_version=$info.product_version;
                connection=$info.connection; client_runtime=$runtimeInfo;
                onboarding_summary=$info.onboarding_summary; capabilities=$info.capabilities;
                next_action='guide_user'; user_message=$messages.connected;
                runtime_message=$(if ($runtimeReady) { $null } else { $messages.runtime_missing })
            } | ConvertTo-Json -Depth 12 -Compress
            exit 0
        }
        if (-not $connection) { $runtime = $info.client_runtime.python }
        if (-not $runtimeInfo.available -or -not $runtime -or -not [IO.Path]::IsPathRooted($runtime) -or
            -not (Test-Path -LiteralPath $runtime -PathType Leaf)) { Finish-Failure 'runtime_missing' }
    } catch {
        $exception = $_.Exception
        while ($exception.InnerException) { $exception = $exception.InnerException }
        if ($exception -is [Net.WebException] -and $exception.Status -eq [Net.WebExceptionStatus]::Timeout) { $failureCode = 'connection_timeout' }
        if ($exception -is [Net.WebException] -and $exception.Response) {
            $status = [int]$exception.Response.StatusCode
            $exception.Response.Dispose()
            if ($status -in @(301, 302, 404, 405)) { $failureCode = 'unexpected_service' } else { $failureCode = 'http_error' }
        }
        Finish-Failure $failureCode
    }
}

try {
    & $runtime (Join-Path $PSScriptRoot 'mediaflow.py') @MediaFlowArguments
    exit $LASTEXITCODE
} catch { Finish-Failure 'runtime_failed' }
