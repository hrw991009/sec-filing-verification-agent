---
{"name":"industry-news-brief","description":"Summarize recent public news for supported industries: fintech, healthcare, energy_power, smart_transport. Use when a user requests an industry news brief; not general web browsing or company filing research."}
---
# Industry news brief

Use industry.web_search within the user's selected industry and the tool's supported source kinds. Do not change industry silently. If scope or time range is unclear, state the limitation or ask a short question. Queries must respect the supplied input schema.

Collect bounded results, deduplicate overlapping stories, then organize a concise brief: event, source/date if returned, relevance, and uncertainty. Cite the actual returned source markers for every externally sourced assertion. Separate source facts from your interpretation. A retrieval timestamp is not a publication date; never invent dates, freshness, missing coverage, or search access. Do not claim a complete market survey from a few hits.

Prefer a small Markdown table when comparing several stories. Preserve provider failures and empty results explicitly; do not replace unavailable news with invented events. This skill does not verify SEC filings, create financial Evidence, calculate returns, or execute a separate workflow. Finish using the runtime's existing final decision envelope.
