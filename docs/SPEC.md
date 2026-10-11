# materially: build spec

What gets built at c0mpiled-15 (build 12:00-19:00). The product is **materially**.

## 1. The product
A drywall/framing back office in one app:
1. **Drawing set → sheets.** A decision model (Jev) sorts the sheets by type.
2. **Partition-type sheets → conditions library.** DeepSeek on Luminal reads each wall type into JSON, and MongoDB validates it.
3. **Condition → product data.** Firecrawl finds the manufacturer sheets, and Jev checks each spec against the condition.
4. **Condition × LF → material order.** Over-purchase guard, priced lines.
5. **Order → PO → supplier inbox**, sent from the app.
6. **Foreman pins an EWO on the drawing** → MongoDB maps the pin to a room → Jev picks the task → the cheapest capable model drafts it → validator → PM queue, live.
7. **Every model call is logged** → cost per job on screen.

## 2. Flow
```
Drawing set ─pdfplumber─► sheets ─[Jev: sheet kind]─► partition sheets
                                                      │ run_task("parse_partition") → DeepSeek (Luminal)
                                                      ▼
                                       conditions ◄─ $jsonSchema (bad → review_queue)
                                         │ Firecrawl → product_data (cached) ─[Jev: spec mismatch?]
                                         ▼
               LF × condition ─ orders/math ─ $lookup price_book ─ $merge ─► orders ─ guard ─► PO → test inbox

Foreman pin ─► ewos {sheet, pin} ─ $near on rooms (2d) ─► room ─[Jev: which task?]
            ─► run_task(task) → DeepSeek draft ─ $jsonSchema ─ (fail → Sonnet retry once) ─► ewo_drafts
            ─ change stream ─► PM queue (SSE)

Every model call ─► usage ─ $group ─► "N decisions on this job: $0.0X"
```

## 3. Stack and repo layout
- **Runtime:** Python 3.11+, one runtime. FastAPI, pymongo, Jinja2 templates with small vanilla JS, and EventSource (SSE) for live updates.
- **Libraries:** pypdfium2 (text segments with positions, and page images; much faster than pdfplumber on dense CAD sheets), pdfplumber as a fallback, Playwright for the demo script.
- **Database:** MongoDB Atlas (M10 for the day, or M0).
- **Models:**
  - Jev `typesafe/jev-1.13` via OpenRouter, TypeSafe SDK, base URL `https://openrouter.ai/api`, `POST /api/v1/systemone`. Docs: https://docs.typesafe.ai/api.md and https://openrouter.ai/docs/guides/community/typesafe-sdk.md
  - Luminal `deepseek-ai/DeepSeek-V4.1-Flash` (OpenAI-compatible; take the base URL from the dashboard). GLM 5.3 Flash only if it's live.
  - Sonnet via the Anthropic API, used only for the retry tier.
- **Web:** Firecrawl for search and scraping.
- **Email:** SMTP from env, falling back to an `outbox` collection when SMTP isn't set.
- **Infra (local, Docker, `infra/`):** `infra/up.ps1` starts self-hosted Infisical (http://localhost:8080) for keys and a LiteLLM proxy (http://localhost:4000) in front of Luminal. LiteLLM serves `deepseek-ai/DeepSeek-V4.1-Flash` capped at 30 requests/min (`infra/litellm/config.yaml`); the app holds only a LiteLLM virtual key, never the Luminal key.

**How keys flow.** Keys live only in Infisical (environment `dev`); nothing secret is in the repo or on disk.
```
Infisical dev /          MONGODB_URI, OPENROUTER_API_KEY, ANTHROPIC_API_KEY, FIRECRAWL_API_KEY,
                         SMTP_*, TEST_INBOX, LITELLM_BASE_URL, LITELLM_API_KEY (virtual key)
   └─ infisical run --env=dev -- <cmd> ─► app: DeepSeek → LiteLLM, Jev → OpenRouter, Sonnet retry → Anthropic
Infisical dev /litellm   LUMINAL_BASE_URL, LUMINAL_API_KEY
   └─ infra/up.ps1: infisical run --path=/litellm -- docker compose up ─► LiteLLM container → Luminal
```
`~/.c15/infra.env` holds only the local containers' bootstrap secrets (made by `infra/init_secrets.py`). A machine without Infisical can put the same names in `~/.c15/keys.env` (or the file named by `C15_KEYS`), which is loaded without overriding the environment.

**DB_NAME rule.** Each lane has its own database: `lane1`, `lane2`, `lane3`, `h1`, `h2`; `demo` is main's (and the default). `DB_NAME` is deliberately not stored in Infisical, because `infisical run` would override a lane's value: set it in the shell first, e.g. `$env:DB_NAME = "lane2"; infisical run --env=dev -- uv run pytest tests/pipeline -q`.

```
repo/
  app/        main.py, settings.py, db.py, jev.py, router.py   ← integrator only
  contracts/  validators, one sample doc per collection, setup script, testids list
  extract/    lane 1     pipeline/  lane 2     ui/  lane 3
  procure/    H1         po/        H1
  orders/     H2         eval/      H2         demo/  H2
  tests/<lane>/
  fixtures/   PDFs gitignored; expected JSON committed (your labels)
```
Each lane exposes its own FastAPI `APIRouter` in its folder. The integrator includes every router in `main.py` once, at skeleton time.

## 4. Collections (fields are the contract)
References between collections (`job_id`, `sheet`, `condition_id`, `room_id`, `ewo_id`) are the referenced doc's `_id` as a hex string. The validators live in `contracts/schemas.py`.

- **jobs**: name, created_at, status (`uploading` | `processing` | `ready` | `error`), files [{name, pages}], pages_total, pages_named, pages_rendered, error.
- **sheets**: job_id, page (1-based across the job), file, file_page, sheet_no, title, name_source (`bookmark` | `title_block` | `none`), kind (`partition_types` | `floor_plan` | `reflected_ceiling` | `elevations` | `details` | `schedules` | `other`), routing{}, size_pt [w, h], scale {label, pt_per_ft, ratio, source (`title_block` | `view_title` | `bookmark` | `manual`), confidence, candidates[]}, image_path, thumb_path, image_scale (px per PDF point), created_at.
- **conditions**: job_id, type_tag (for example "A1"), studs {size_in, gauge, spacing_in}, layers_each_side [{thickness_in, board_type}], sides (1 or 2), insulation, fire_rating, ul_assembly, stc, height_ft, source {sheet, page, bbox}, routing{}. Required: type_tag, studs, layers_each_side. Enums on gauge and board_type.
- **rooms**: job_id, sheet, name, number, label_pt [x, y]. Index `{label_pt: "2d", sheet: 1}` with `min: 0, max: 5000`; the default bounds are ±180, too small for PDF points.
- **ewos**: job_id, sheet, pin [x, y], room_id, description, submitted_by ("foreman, synthetic"), status, created_at.
- **ewo_drafts**: ewo_id, task, model, line_items [{desc, qty, unit, unit_cost}], total, explanation, routing{}, escalated (bool).
- **orders**: job_id, condition_id, lf, height_ft, lines [{item, qty, unit, unit_cost, computed_qty}], total, guard {over (bool), items_over []}, supplier_email, sent_at.
- **product_data**: query_hash (unique), condition_id, component, url, title, attrs {thickness_in, board_type, ul, gauge, ...}, spec_check {attr: p_mismatch}, fetched_at.
- **price_book**: item, unit, unit_cost, supplier ("Sample Supply Co"). Made-up numbers.
- **model_policy**: task, tier (`decide` | `quick` | `expert`), model, retry_model, enabled. The admin edits it and the router reads it live.
- **usage**: job_id, task, model, input_tokens, output_tokens, cost_usd, ms, at. Index on job_id.
- **review_queue**: source, doc, reason, at.

Every collection with AI-written data uses `validationAction: "error"`. On a write error, the doc goes to `review_queue` with the reason.

## 5. MongoDB features (all visible in the demo)
1. `$jsonSchema` validators reject bad AI output (conditions, ewo_drafts, orders).
2. `2d` index on drawing coordinates: an EWO pin → `$near` → nearest room label on the same sheet.
3. A change stream on `ewo_drafts` feeds the PM queue over SSE.
4. Aggregation: order lines `$lookup` price_book and `$merge` into orders. The guard compares ordered vs computed × (1 + waste).
5. The `model_policy` collection: change a model there and the router follows, with no code change.
6. `usage` + `$group`: cost per job.
7. A product-data cache keyed by query hash, so the demo doesn't depend on live scraping.

## 6. Jev and the router
**Jev only picks; it never writes.** Three jobs:

| Job | Question | Type |
|---|---|---|
| Sheet sort | From the title-block text, which sheet kind is this? | Choice over the sheet kinds |
| Spec check | Does this product fail the condition's spec for <attribute>? (one question per attribute) | Noul (yes/no). Flag when P(yes) > 0.7, tuned on your examples. |
| EWO route | Which task handles this EWO? Options from `model_policy` tasks: `price_extra`, `base_scope_check`, `backcharge`. | Choice. Anything unclear goes to review_queue. |

**Rules:**
- Ask every Choice twice with shuffled options, because Jev favors the first option. Accept only when both answers agree; otherwise send to review_queue.
- Store `routing {choice, probabilities, model, ms, usage}` on the doc.
- Fallback models use the same request format: `cloudflare/clef-flash`, `jaredpalmer/kev-4b`, `inception/mercury-decide:free`. Last resort is plain rules.

**`run_task(task, payload, validator)`:**
1. Look up `model_policy` for the task.
2. Call the cheapest model (DeepSeek).
3. Validate the result.
4. On failure, retry once with `retry_model` (Sonnet) and set escalated = true.
5. Log a `usage` row for every call.

**Not Jev:** math (quantities, the guard) is aggregation and code. Writing (drafts, the PO) and reading partition text into JSON are DeepSeek's jobs.

## 7. Order math (fill in before 12:00)
The defaults below are starting points; replace them with your numbers. Inputs: LF, height_ft, stud spacing_in, layers_each_side, sides.

| Item | Formula | Default |
|---|---|---|
| Studs (pcs) | ceil(LF × 12 / spacing_in) + 1 per run, plus extras at openings and corners | your rule |
| Track (pcs) | LF × 2 / track length | 10 ft lengths |
| Board (sheets) | LF × height × layers_each_side × sides / sheet area | 4×8 = 32 sf; 4×10 = 40; 4×12 = 48 |
| Screws | | **you fill** |
| Mud | | **you fill** |
| Tape | | **you fill** |
| Corner bead | | **you fill** |
| Insulation | | **you fill** |
| Waste % | per item | **you fill** |

**3 hand-worked cases** (these become H2's test answers):
1. A1, 100 LF, 10 ft, 3-5/8" 20 ga at 16", one layer 5/8" Type X each side → studs __, track __, board __, ...
2. __
3. __

## 8. Extraction (lane 1)
1. **Per page:**
   - Get words with pdfplumber.
   - Find the title block: the densest text cluster in the right strip or the bottom-right corner.
   - Jev sorts the sheet from the title text.
   - Render a PNG with pypdfium2 for the viewer.
2. **On partition sheets:**
   - Find type tags: short tokens like `A1`, `P-3`, `1A`, `3B`, usually inside a small symbol.
   - Group the words near each tag into one block.
   - `run_task("parse_partition", block_text)` → condition JSON → validator → conditions, or review_queue.
3. **On floor plans:** room-number tokens (for example `101`, `204A`) next to uppercase words become rooms, with label_pt at the label's center.
4. **Out of scope today:**
   - Scanned sheets.
   - Text exported as outlines (pdfplumber finds no words).
   - Automatic measuring. LF is typed in; click-to-measure only if there's time.

## 9. Procurement and PO (H1)
- **Per component** (board, studs, insulation):
  1. Build a query, for example "5/8 Type X gypsum board product data sheet".
  2. Firecrawl search, top 3 results, scrape them.
  3. `run_task("extract_product_attrs")` pulls out the product's attributes.
  4. Jev runs the spec check on each attribute.
  5. Store in product_data. A cache hit makes zero Firecrawl calls.
- **PO:** render HTML (a PDF is optional) from the order and send it to `TEST_INBOX` over SMTP. If SMTP isn't set, write to `outbox` and show it.

## 10. Measurements for the video
- **Partition accuracy:** field-level, compared with your expected JSON. Covers stud size, gauge, spacing, layers, board type, fire rating; 3 types × 3 sets.
- **Sheet-sort accuracy:** Jev's kind against your label for each sheet.
- **Cost per job:** `$group` on usage.
- **Optional:** Jev EWO-routing accuracy on synthetic EWOs that you label yourself. Never use model-made labels as ground truth.

## 11. README (MongoDB article shape)
1. Team + thanks
2. The problem
3. Architecture (a JSON block)
4. MongoDB as the persistence layer (the 7 features)
5. Sample documents
6. Why not SQL
7. What's next: production tracking (installed LF vs takeoff), schedule checks, offline foreman capture, more trades
8. GitHub link + video
