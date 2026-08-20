[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI was not found."
}

$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$composeFile = Join-Path $repositoryRoot "docker-compose.yml"

Push-Location $repositoryRoot
try {
    & docker compose --file $composeFile down --remove-orphans
    if ($LASTEXITCODE -ne 0) {
        throw "EMC_RAG shutdown failed."
    }
}
finally {
    Pop-Location
}

Write-Host "EMC_RAG containers stopped. Named data and model volumes were preserved."
