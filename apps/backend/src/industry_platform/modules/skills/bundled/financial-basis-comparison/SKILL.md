---
{"name":"financial-basis-comparison","description":"Check whether user-supplied financial figures or excerpts use comparable reporting bases. Use for checking periods, currency, scale, consolidated/parent scope and stock/flow distinctions before comparison, without calculating ratios."}
---
# Financial reporting basis comparison

Use only supplied material. Request missing excerpts rather than searching unrelated industry news as a substitute for filings. This is a comparability checklist, not an audit opinion or verified calculation.

Check: entity and consolidation scope; fiscal dates and annual versus quarterly/YTD periods; currency and scale; accounting basis and restatements; revenue versus profit; parent versus total net income/equity; point-in-time balances versus period flows; opening, closing and average balances. Mention only distinctions relevant to the supplied figures.

Return a concise Markdown table with dimension, each figure's stated basis, comparability (comparable / not comparable / insufficient information), and required clarification. Preserve original figures; do not silently convert units, annualize quarters, substitute end balances for averages, or compute financial ratios. Missing data is not zero. Distinguish a confirmed mismatch from an unknown basis. If formal filing evidence or DuPont calculation is needed, direct the user to the built-in Research task without claiming to invoke it. Finish using the runtime's existing final decision envelope.
