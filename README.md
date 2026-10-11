# materially

Drywall/framing back office on MongoDB: drawing set → sheets → partition types become a validated conditions
library → product data checked → material order with an over-purchase guard → PO to the supplier, plus foreman
EWOs pinned on the drawing and routed live to the PM. Built at c0mpiled-15. The plan is in [docs/SPEC.md](docs/SPEC.md);
the data contracts are in [contracts/README.md](contracts/README.md).

*Quickstart for the team; the full write-up replaces this later.*

## Quickstart (Windows, PowerShell)
1. Install Docker Desktop, [uv](https://docs.astral.sh/uv/) and the Infisical CLI, and start Docker Desktop.
2. Start the local infra (Infisical on http://localhost:8080, LiteLLM on http://localhost:4000):
   ```powershell
   .\infra\up.ps1
   ```
   On first boot it generates `~/.c15/infra.env`. After you've logged in (next step) and stored the Luminal keys
   under the Infisical folder `/litellm`, run it again so LiteLLM picks them up.
3. Log in to the self-hosted Infisical (the project id is in `.infisical.json`):
   ```powershell
   infisical login --domain http://localhost:8080/api
   ```
4. Install dependencies:
   ```powershell
   uv sync
   ```
5. Run the app with every key injected from Infisical, then open http://localhost:8000:
   ```powershell
   .\scripts\dev.ps1
   ```

## Everyday commands
Every command that needs keys runs under `infisical run --env=dev -- ...`. Set `DB_NAME` first (`lane1`, `lane2`,
`lane3`, `h1`, `h2` or `demo`, the default); it's deliberately not stored in Infisical.
```powershell
uv run pytest -q                                                    # offline tests (mongomock)
$env:DB_NAME = "demo"; infisical run --env=dev -- uv run pytest -q  # also runs live_db tests on Atlas
infisical run --env=dev -- uv run python -m scripts.smoke           # one Jev call + one run_task, with usage rows
infisical run --env=dev -- uv run python -m contracts.setup_db      # validators, indexes, seeds on all six DBs
```

## Layout
| Folder | Owner |
|---|---|
| `app/`, `contracts/`, `infra/`, `scripts/`, `docs/` | integrator |
| `extract/` | lane 1: drawing set → sheets, conditions, rooms |
| `pipeline/` | lane 2: EWOs, orders, PM queue, cost |
| `ui/` | lane 3: screens |
| `procure/`, `po/` | H1: product data, PO |
| `orders/`, `eval/`, `demo/` | H2: order math, accuracy, demo script |

Each lane works in its own worktree and branch (`lane/<name>`). Requests for changes in `app/` or `contracts/` go
in [LANE_NOTES.md](LANE_NOTES.md). Drawing PDFs and keys never go in git.
