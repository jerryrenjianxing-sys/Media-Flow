param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $MediaFlowArguments
)

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
        Write-Error 'MediaFlow Skill config is not valid JSON.'
        exit 2
    }
}

if (-not $runtime) {
    try {
        $api = if ($env:MEDIAFLOW_API_URL) { $env:MEDIAFLOW_API_URL } elseif ($config.api_url) { $config.api_url } else { 'http://127.0.0.1:48138' }
        $uri = [Uri]$api
        $ip = $null
        $local = $uri.Host -eq 'localhost' -or ([Net.IPAddress]::TryParse($uri.DnsSafeHost, [ref]$ip) -and [Net.IPAddress]::IsLoopback($ip))
        if (-not $local -or $uri.Scheme -ne 'http' -or $uri.UserInfo -or $uri.Query -or $uri.Fragment -or $uri.AbsolutePath -notin @('', '/', '/api/automation')) {
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
        $response = $request.GetResponse()
        try {
            if ([int]$response.StatusCode -ne 200) { throw 'Platform discovery failed.' }
            $reader = New-Object IO.StreamReader($response.GetResponseStream())
            try { $info = $reader.ReadToEnd() | ConvertFrom-Json } finally { $reader.Dispose() }
        } finally { $response.Dispose() }
        $runtime = $info.client_runtime.python
        if (-not $info.client_runtime.available -or -not $runtime -or -not [IO.Path]::IsPathRooted($runtime) -or -not (Test-Path -LiteralPath $runtime -PathType Leaf)) {
            throw 'Platform runtime unavailable.'
        }
    } catch {
        Write-Error 'Cannot read the MediaFlow runtime. Start MediaFlow, then check/repair the platform in Task Console and retry. Older platforms need an update or an explicitly configured Python. Do not reinstall developer tools.'
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
