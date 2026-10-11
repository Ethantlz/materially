# Bring up local Infisical (http://localhost:8080) and LiteLLM (http://localhost:4000/ui).
# Local infra secrets come from ~/.c15/infra.env; the Luminal key comes from Infisical (dev, /litellm) once you're logged in.
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $HOME ".c15\infra.env"
$compose = Join-Path $PSScriptRoot "docker-compose.yml"
if (-not (Test-Path $envFile)) { uv run --project $root python (Join-Path $PSScriptRoot "init_secrets.py") }

$infisicalUp = $false
try { $infisicalUp = (Invoke-WebRequest http://localhost:8080/api/status -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200 } catch {}
if ($infisicalUp -and (Get-Command infisical -ErrorAction SilentlyContinue)) {
    Push-Location $root
    try { infisical run --env=dev --path=/litellm --silent -- docker compose --env-file $envFile -f $compose up -d }
    finally { Pop-Location }
} else {
    # First boot (Infisical not up yet): start everything; rerun this script afterwards to load the Luminal key.
    docker compose --env-file $envFile -f $compose up -d
}
docker compose --env-file $envFile -f $compose ps
