# Bring up local Infisical (http://localhost:8080) and LiteLLM (http://localhost:4000/ui).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $HOME ".c15\infra.env"
if (-not (Test-Path $envFile)) { uv run --project $root python (Join-Path $PSScriptRoot "init_secrets.py") }
docker compose --env-file $envFile -f (Join-Path $PSScriptRoot "docker-compose.yml") up -d
docker compose --env-file $envFile -f (Join-Path $PSScriptRoot "docker-compose.yml") ps
