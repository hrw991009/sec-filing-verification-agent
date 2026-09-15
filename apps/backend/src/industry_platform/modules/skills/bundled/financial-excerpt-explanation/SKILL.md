---
{"name":"financial-excerpt-explanation","description":"Explain a financial statement excerpt supplied by the user, distinguishing reported facts, accounting terms and uncertain interpretation. Use for lightweight reading assistance, not independently verified financial research."}
---
# Explain a supplied financial excerpt

Work only from material actually supplied in the conversation or successfully read attachments. If the excerpt is absent or unreadable, request it. Do not pretend to have opened a filing, accessed private knowledge, or verified the source externally.

Identify the entity, fiscal period, currency, scale and statement type when present. Explain the important line items in plain language, quote only short necessary spans, and distinguish the excerpt's statements from your interpretation. Mark missing context explicitly. Do not invent causality, source citations, operating drivers or investment recommendations.

Do not infer tax-inclusive amounts, tax treatment, or detailed revenue-recognition policies from a bare revenue label. If the accounting or tax basis is absent, say it is not supplied. Explain revenue as reported revenue, not as cash collected or an assumed tax-inclusive sales total.

For multiple items, a small Markdown table may contain original item, plain-language meaning, and caveat. Preserve reported figures and units exactly. Do not compute new financial results yourself. If the user asks for a verified ratio, DuPont analysis or filing-level conclusions, explain that this lightweight mode cannot provide that verification and direct them to the Research task UI without claiming to start it. Finish using the runtime's existing final decision envelope.
