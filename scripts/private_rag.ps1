[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("prepare", "build-cases", "ingest", "evaluate")]
    [string]$Action,
    [Parameter(Mandatory = $true)]
    [string]$InputFile,
    [string]$ModelDir = "models/deepdoc",
    [string]$ElasticsearchUrl = "http://127.0.0.1:19200",
    [ValidateSet("dev", "holdout")]
    [string]$Stage = "dev",
    [string]$CasesFile,
    [string]$CondaEnvironment = "cvrag"
)

$ErrorActionPreference = "Stop"

function Import-DashScopeApiKeyFromDotEnv {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not [string]::IsNullOrWhiteSpace($env:DASHSCOPE_API_KEY) -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return
    }
    $match = Select-String -LiteralPath $Path -Pattern '^\s*(?:export\s+)?DASHSCOPE_API_KEY\s*=\s*(?<value>.*)\s*$' | Select-Object -First 1
    if ($null -eq $match) {
        return
    }
    $value = $match.Matches[0].Groups["value"].Value.Trim()
    if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    if (-not [string]::IsNullOrWhiteSpace($value)) {
        $env:DASHSCOPE_API_KEY = $value
    }
}

function Resolve-CondaPython {
    param([Parameter(Mandatory = $true)][string]$EnvironmentName)

    $resolved = @(
        & conda run -n $EnvironmentName python -c "import sys; print(sys.executable)" |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    if ($LASTEXITCODE -ne 0 -or $resolved.Count -ne 1 -or -not (Test-Path -LiteralPath $resolved[0] -PathType Leaf)) {
        throw "Private Python environment is unavailable."
    }
    return $resolved[0]
}

$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$privateRoot = [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot "datasets/private_local"))
$inputPath = if ([System.IO.Path]::IsPathRooted($InputFile)) { [System.IO.Path]::GetFullPath($InputFile) } else { [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot $InputFile)) }
$modelPath = if ([System.IO.Path]::IsPathRooted($ModelDir)) { [System.IO.Path]::GetFullPath($ModelDir) } else { [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot $ModelDir)) }

if (-not ($inputPath.StartsWith($privateRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase))) {
    throw "InputFile must resolve below datasets/private_local."
}
if (-not (Test-Path -LiteralPath $inputPath -PathType Leaf)) {
    throw "Private input file is unavailable."
}
if ($Action -in @("ingest", "evaluate")) {
    Import-DashScopeApiKeyFromDotEnv -Path (Join-Path $repositoryRoot ".env")
    if ([string]::IsNullOrWhiteSpace($env:DASHSCOPE_API_KEY)) {
        throw "DASHSCOPE_API_KEY must be supplied through the process environment or local .env."
    }
}
if ($Action -eq "evaluate" -and [string]::IsNullOrWhiteSpace($CasesFile)) {
    throw "CasesFile must be supplied for private evaluation."
}
$casesPath = $null
if (-not [string]::IsNullOrWhiteSpace($CasesFile)) {
    $casesPath = if ([System.IO.Path]::IsPathRooted($CasesFile)) { [System.IO.Path]::GetFullPath($CasesFile) } else { [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot $CasesFile)) }
    if (-not ($casesPath.StartsWith($privateRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase))) {
        throw "CasesFile must resolve below datasets/private_local."
    }
    if (-not (Test-Path -LiteralPath $casesPath -PathType Leaf)) {
        throw "Private cases file is unavailable."
    }
}

Push-Location $repositoryRoot
try {
    $arguments = @($Action, "--input", $inputPath, "--private-root", $privateRoot, "--model-dir", $modelPath, "--elasticsearch-url", $ElasticsearchUrl, "--stage", $Stage)
    if ($null -ne $casesPath) {
        $arguments += @("--cases", $casesPath)
    }
    $pythonExecutable = Resolve-CondaPython -EnvironmentName $CondaEnvironment
    & $pythonExecutable backend/scripts/private_rag.py @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Private RAG $Action failed. Inspect only the content-free private report status."
    }
}
finally {
    Pop-Location
}
