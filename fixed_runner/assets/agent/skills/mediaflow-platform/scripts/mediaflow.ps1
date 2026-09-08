param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $MediaFlowArguments
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$checking = $MediaFlowArguments.Count -gt 0 -and $MediaFlowArguments[0] -eq 'check'
function Finish-CheckFailure([string] $Code, [string] $Message) {
    [ordered]@{ ok=$false; reason_code=$Code; service_state='unknown';
        next_action='ask_user'; user_message=$Message } | ConvertTo-Json -Depth 8 -Compress
    exit 2
}

$runtime = $env:MEDIAFLOW_PYTHON
$config = @{}
$configPath = if ($env:MEDIAFLOW_SKILL_CONFIG) {
    $env:MEDIAFLOW_SKILL_CONFIG
} else {
    Join-Path (Split-Path -Parent $PSScriptRoot) 'config.json'
}
for ($index = 0; $index -lt $MediaFlowArguments.Count; $index++) {
    if ($MediaFlowArguments[$index] -eq '--config' -and $index + 1 -lt $MediaFlowArguments.Count) {
        $configPath = $MediaFlowArguments[$index + 1]
    } elseif ($MediaFlowArguments[$index].StartsWith('--config=')) {
        $configPath = $MediaFlowArguments[$index].Substring(9)
    }
}

if (Test-Path -LiteralPath $configPath -PathType Leaf) {
    try {
        $config = Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json
        if (-not $runtime) { $runtime = $config.python }
    } catch {
        if ($checking) { Finish-CheckFailure 'invalid_config' 'Skill config is not valid JSON. Ask the user before changing it.' }
        Write-Error 'MediaFlow Skill config is not valid JSON.'
        exit 2
    }
}

if ($checking -or -not $runtime) {
    $failureCode = 'connection_failed'
    try {
        $api = if ($env:MEDIAFLOW_API_URL) { $env:MEDIAFLOW_API_URL } elseif ($config.api_url) { $config.api_url } else { 'http://127.0.0.1:48138' }
        $uri = [Uri]$api
        $ip = $null
        $local = $uri.Host -eq 'localhost' -or ([Net.IPAddress]::TryParse($uri.DnsSafeHost, [ref]$ip) -and [Net.IPAddress]::IsLoopback($ip))
        if (-not $local -or $uri.Scheme -ne 'http' -or $uri.UserInfo -or $uri.Query -or $uri.Fragment -or $uri.AbsolutePath -notin @('', '/', '/api/automation')) {
            $failureCode = 'invalid_address'
            throw 'MediaFlow API must use loopback HTTP.'
        }
        $builder = [UriBuilder]$uri
        if ($builder.Host -eq 'localhost') { $builder.Host = '127.0.0.1' }
        $builder.Path = '/api/automation'
        $request = [Net.HttpWebRequest]::Create($builder.Uri)
        $request.Proxy = $null
        $request.AllowAutoRedirect = $false
        $request.Timeout = 5000
        $request.ReadWriteTimeout = 5000
        $clock = [Diagnostics.Stopwatch]::StartNew()
        $response = $request.GetResponse()
        try {
            $failureCode = 'unexpected_service'
            if ([int]$response.StatusCode -ne 200) { throw 'Platform discovery failed.' }
            $reader = New-Object IO.StreamReader($response.GetResponseStream())
            try {
                # One deadline includes headers AND streamed body (not per-chunk timeouts).
                $buffer = New-Object char[] 4096
                $text = New-Object Text.StringBuilder
                while ($true) {
                    $reading = $reader.ReadAsync($buffer, 0, $buffer.Length)
                    $remaining = [Math]::Max(0, 5000 - [int]$clock.ElapsedMilliseconds)
                    if ($remaining -le 0 -or -not $reading.Wait($remaining)) {
                        $failureCode = 'connection_timeout'
                        $request.Abort()
                        throw 'Discovery timed out.'
                    }
                    $count = $reading.Result
                    if ($count -eq 0) { break }
                    if ($text.Length + $count -gt 1048576) {
                        $request.Abort()
                        throw 'Discovery response too large.'
                    }
                    [void]$text.Append($buffer, 0, $count)
                }
                $info = $text.ToString() | ConvertFrom-Json
            } finally { $reader.Dispose() }
        } finally { $response.Dispose() }
        if ($checking) {
            if ($info.product -ne 'MediaFlow' -or $info.api_version -ne '1' -or $info.status -ne 'available') {
                Finish-CheckFailure 'unexpected_service' 'Response is not a supported MediaFlow first-contact endpoint. Check the address or platform version; do not start another service.'
            }
            [ordered]@{ ok=$true; reason_code='connected'; service_state='running';
                api_url=$builder.Uri.GetLeftPart([UriPartial]::Authority);
                product_version=$info.product_version; client_runtime=$info.client_runtime;
                onboarding=$info.onboarding; next_action='guide_user';
                user_message='MediaFlow is connected. Report the stored snapshot with its timestamps, then guide the user. Device online state is not verified.'
            } | ConvertTo-Json -Depth 12 -Compress
            exit 0
        }
        $runtime = $info.client_runtime.python
        if (-not $info.client_runtime.available -or -not $runtime -or -not [IO.Path]::IsPathRooted($runtime) -or -not (Test-Path -LiteralPath $runtime -PathType Leaf)) {
            throw 'Platform runtime unavailable.'
        }
    } catch {
        if ($checking) {
            $exception = $_.Exception
            while ($exception.InnerException) { $exception = $exception.InnerException }
            if ($exception -is [Net.WebException] -and $exception.Status -eq [Net.WebExceptionStatus]::Timeout) {
                $failureCode = 'connection_timeout'
            }
            if ($exception -is [Net.WebException] -and $exception.Response) {
                $status = [int]$exception.Response.StatusCode
                $exception.Response.Dispose()
                if ($status -in @(301, 302, 404, 405)) { $failureCode = 'unexpected_service' } else { $failureCode = 'http_error' }
            }
            if ($failureCode -eq 'unexpected_service') {
                Finish-CheckFailure $failureCode 'The endpoint responded but is not a supported MediaFlow first-contact service. Check the address or platform version; do not start another service.'
            }
            Finish-CheckFailure $failureCode 'Cannot confirm the MediaFlow connection; this does not prove the service is stopped. Ask the user before starting it. No service or task was started.'
        }
        Write-Error 'Cannot read the MediaFlow runtime. Ask the user before starting MediaFlow; an unreachable API does not prove the service is stopped. Older platforms need an update or an explicitly configured Python. Do not reinstall developer tools.' -ErrorAction Continue
        exit 2
    }
}

try {
    $ErrorActionPreference = 'Stop'
    & $runtime (Join-Path $PSScriptRoot 'mediaflow.py') @MediaFlowArguments
    exit $LASTEXITCODE
} catch {
    Write-Error 'Configured MediaFlow Python could not run. Check the configured runtime or repair MediaFlow; no task result was confirmed.' -ErrorAction Continue
    exit 2
}
