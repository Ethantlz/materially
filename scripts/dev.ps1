# Run the app with every key injected from Infisical (dev, root folder). http://localhost:8000
# Any other command works the same way:  infisical run --env=dev -- uv run python -m contracts.setup_db
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try { infisical run --env=dev --path=/ -- uv run uvicorn app.main:app --reload --port 8000 }
finally { Pop-Location }
