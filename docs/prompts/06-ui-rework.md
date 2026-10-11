REWORK for lane/ui (worktree C:\Users\ethan\materially-ui, DB lane3). Keep every screen you've built and every data-testid in contracts/TESTIDS.md working; the Playwright demo depends on them. Re-lay them out and restyle them, don't delete them. This turns the app into a TakeOps-style takeoff tool.

First: `git pull --rebase origin main` for the new `measurements` contract (contracts/schemas.py, b923394).

Video beats, in order (design for these, 1920x1080, no terminal on screen):
1. Upload a drawing set; sheet names and scales appear.
2. Conditions library builds from the partition sheet; one bad parse is rejected into review.
3. Takeoff: pick condition A1 and click along walls on a floor plan; LF counts up live.
4. Quantities table, then create the order; a padded line trips the over-purchase guard.
5. Send the PO: sent state plus PO preview.
6. A foreman pins an EWO on a phone; the PM queue updates live.
7. The cost badge.

Look and feel (a desktop takeoff tool, dense and calm):
- Dark app chrome (#15171c / #1e2128, text #e6e8eb) around a light drawing canvas. One accent colour for actions; conditions keep their own colours.
- A top bar: project name, tabs (Sheets | Conditions | Takeoff | Orders | EWO | PM | Review), cost badge on the right.
- Takeoff workspace /jobs/{job_id}/takeoff?sheet=<id>:
  - Left rail: sheets grouped by discipline prefix (A, S, M...), each with a thumbnail, number, title and a scale chip (amber warning when there's no scale). Partition sheets are highlighted.
  - Centre: the canvas.
  - Right panel: conditions with a colour swatch, name and running LF / SF / EA. Click one to make it active; "+ New condition" opens a small form.
  - Bottom drawer: the quantities table.
- Toolbar plus shortcuts: Select (V), Linear (L), Area (A), Count (C), Calibrate (K), Fit (F), Undo (Ctrl+Z), Esc to finish or cancel. Scroll to zoom at the cursor, space-drag or middle-drag to pan.
- Phone layout only for the EWO pin page.

Canvas mechanics:
- Draw the sheet image (image_path) with an SVG overlay inside one CSS-transformed container.
- Convert screen -> image px -> PDF points with the zoom/pan transform and the sheet's image_scale (px per pt). Store and send PDF points only.
- Existing measurements draw in their condition's colour.
- Linear: click points; a live readout shows feet-and-inches (from scale.pt_per_ft) while drawing; double-click or Enter finishes and POSTs /api/measurements. Area and count work the same way.
- Select a measurement and press Delete to remove it.
- Calibrate: click two points, type the real length, POST /api/sheets/{id}/calibrate.
- Clicking a condition's "source" jumps to its partition sheet with the bbox highlighted.

Quantities -> order: GET /api/jobs/{id}/quantities refreshes after every measurement. A "Create order" button on each condition row calls POST /api/jobs/{id}/conditions/{cid}/order and opens your existing order page (editable qty, guard warning). "Send PO" calls POST /api/orders/{id}/send, then shows the sent state and GET /api/orders/{id}/po in a preview panel.

Endpoints land from the other lanes:
- lane/extract: GET /api/sheets/{id}, calibrate, condition CRUD with name/color
- lane/pipeline: measurements, quantities, order-from-takeoff, PO, EWO, /events/pm
Until they merge, keep building against contracts/samples.py and ui/demo_seed.py, and show a clear empty state when an endpoint 404s. Don't edit extract/ or pipeline/.

Append every new testid to contracts/TESTIDS.md: canvas, each tool button, condition rows, the active-condition marker, quantities rows, create-order, calibrate, the scale chip.

Done when `uv run pytest tests/ui -q` passes (each screen renders with sample docs, every TESTIDS.md id exists on its page), and the takeoff page at 1920x1080 lets you measure a line on the synthetic A-101 sheet (scripts/make_drawing_set.py) and see LF update. Commit after each step; push lane/ui.
