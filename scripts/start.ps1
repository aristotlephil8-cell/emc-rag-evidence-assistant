[CmdletBinding()]
param(
    [ValidateSet("fake", "dashscope")]
    [string]$Provider,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

if (-not $PSBoundParameters.ContainsKey("Provider")) {
    $Provider = if ($env:CVRAG_PROVIDER) { $env:CVRAG_PROVIDER } else { "fake" }
}
if ($Provider -notin @("fake", "dashscope")) {
    throw "Provider must be 'fake' or 'dashscope'."
}
if ($Provider -eq "dashscope" -and [string]::IsNullOrWhiteSpace($env:DASHSCOPE_API_KEY)) {
    throw "DashScope mode requires DASHSCOPE_API_KEY in the current process environment."
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI was not found. Install Docker Desktop and start Docker Engine."
}

$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$composeFile = Join-Path $repositoryRoot "docker-compose.yml"
$env:CVRAG_PROVIDER = $Provider
$env:CVRAG_REQUIRE_EVALUATION_LOCK = if ($Provider -eq "dashscope") { "true" } else { "false" }
if ($Provider -eq "dashscope") {
    $reportPath = Join-Path $repositoryRoot "artifacts/evaluation/latest.json"
    if (-not (Test-Path -LiteralPath $reportPath -PathType Leaf)) {
        throw "DashScope mode requires a passed artifacts/evaluation/latest.json runtime lock."
    }
}

Push-Location $repositoryRoot
try {
    & docker compose --file $composeFile config --quiet
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose config failed."
    }

    $arguments = @("compose", "--file", $composeFile, "up", "--detach", "--wait")
    if (-not $SkipBuild) {
        $arguments += "--build"
    }
    & docker @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "EMC_RAG startup failed. Inspect with: docker compose --file docker-compose.yml ps"
    }
}
finally {
    Pop-Location
}

Write-Host "EMC_RAG is ready (provider=$Provider)."
Write-Host "Frontend: http://localhost:5173"
Write-Host "Backend:  http://localhost:8000"
