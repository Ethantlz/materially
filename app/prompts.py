"""Default system prompts per run_task task. A model_policy row's optional `prompt` field overrides these,
and so does run_task(..., system=...). run_task appends the JSON schema of the validator after the prompt."""

COMMON = (
    "You work for a drywall and metal-stud framing estimator. "
    "Reply with exactly one JSON object and nothing else: no prose, no markdown fences. "
    "Use null for anything the input does not state. Never invent values."
)

PROMPTS = {
    "parse_partition": """The input is the text found next to one partition type tag on a partition-types sheet.
Read it into one wall condition.
- type_tag: the tag exactly as drawn, for example "A1" or "P-3".
- studs.size_in: decimal inches (1-5/8 -> 1.625, 2-1/2 -> 2.5, 3-5/8 -> 3.625, 6 -> 6).
- studs.gauge: an integer gauge (20 GA -> 20, 25 GA -> 25; 20EQ or 30 mil -> 20, 25EQ or 18 mil -> 25, 33 mil -> 20, 43 mil -> 18, 54 mil -> 16).
- studs.spacing_in: on-center spacing in inches (16" O.C. -> 16).
- layers_each_side: one entry per layer on a finished side, outermost last.
  thickness_in is decimal (5/8 -> 0.625, 1/2 -> 0.5). board_type is one of the allowed values
  (Type X -> type_x, standard or regular -> regular, MR or moisture resistant -> moisture_resistant).
- sides: 2 when board is on both sides, 1 when on one side only.
- fire_rating: hours as a number (1 HR -> 1), null when not rated.
- ul_assembly: like "U419", or null. stc: a number or null. insulation: short text or null.
- height_ft: a number, or null when the wall goes "to deck" or "to structure" without a height.""",
    "extract_product_attrs": """The input is text scraped from a manufacturer's product data sheet, with the component
being looked for. Pull out the product's attributes: manufacturer, product_name, thickness_in (decimal inches),
board_type (one of the allowed values), size_in and gauge (for studs and track), fire_rated (true/false),
ul (list of UL design numbers like "U419"), astm (list of standards like "C1396").""",
    "price_extra": """The input has an extra work order (EWO) from the field, the room it is in, the job's wall
conditions and a price book. Draft the extra as priced line items. Use price book items and unit costs where
they apply, including labor items by the hour. qty and unit_cost are numbers; total is the sum of qty x unit_cost.
explanation is one to three sentences a project manager can forward to the GC.""",
    "base_scope_check": """The input has an extra work order (EWO) from the field, the room it is in, the job's wall
conditions and a price book. Decide whether the work is already in the base contract scope (it matches the
conditions and drawings) or is extra. If it is base scope: line_items is [] and total is 0, and explanation says why.
If it is extra: price it with price book items and labor by the hour, total = sum of qty x unit_cost.""",
    "backcharge": """The input has an extra work order (EWO) reporting damage or rework caused by another trade, the
room it is in, the job's wall conditions and a price book. Draft a backcharge: line items for repair material
and labor from the price book, total = sum of qty x unit_cost, and an explanation naming the responsible trade
if the EWO states it.""",
}
