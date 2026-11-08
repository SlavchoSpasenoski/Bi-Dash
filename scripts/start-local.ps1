$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path -LiteralPath '.env')) {
    $cookieSecret = [Convert]::ToHexString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
    $dataSecret = [Convert]::ToHexString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
    @("REDASH_COOKIE_SECRET=$cookieSecret", "REDASH_SECRET_KEY=$dataSecret", 'OPENAI_API_KEY=', 'REDASH_AI_PROVIDER=ollama', 'REDASH_AI_MODEL=qwen2.5-coder:7b', 'REDASH_OLLAMA_URL=http://ollama:11434') | Set-Content -LiteralPath '.env' -Encoding utf8
}
function Invoke-Compose {
    & docker compose -f compose.local.yaml @args
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed: $args" }
}
Invoke-Compose build server
Invoke-Compose up -d postgres redis
Invoke-Compose run --rm server create_db
Invoke-Compose up -d
Write-Host 'For a new Ollama volume, install the local model once: docker compose -f compose.local.yaml exec ollama ollama pull qwen2.5-coder:7b'
Write-Host 'Open http://localhost:5001/setup to create your Bi-Dash administrator.'
