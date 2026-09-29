# SelectaRank

A rule-based "how likely is an AI agent to pick this service?" score for paid x402 endpoints, plus the data collectors and a static ranking site.

- Live ranking site: https://selectarank.vercel.app
- Live data/badges: https://selectarank-data.vercel.app

## What is in here

| Path | What it does |
|---|---|
| `collector.py` | Pulls every listing from the public x402 Bazaar discovery API and the public MCP Registry (`/v0/servers`). No auth needed. Writes to `data/`. |
| `scorer.py` | Computes a deterministic 0-100 score per listing from six features (speed, description quality, price, traction, freshness, metadata). An optional LLM "simulated selection" step is skipped unless `OPENROUTER_API_KEY` / `ANTHROPIC_API_KEY` is set. |
| `analyze.py` | Cross-tabulates x402 sellers against MCP Registry servers (hostname match). |
| `badge/` | Generates shields.io "endpoint" badge JSON, one per host. See `badge/SPEC.md`. |
| `site/` | `build_site.py` + template for the static ranking page. |
| `dist/` | The prebuilt static site as deployed (page + 2,038 badge JSON files). |
| `out/` | Result summaries from the 2026-09-29 run (score summary, cross-ecosystem summary, top/bottom 50, response-time sample). |

## Run

```
pip install requests
python collector.py both          # ~19k Bazaar listings + ~37k MCP servers into data/ (about 140 MB; not committed)
python scorer.py --reuse-speed    # writes out/scored_all.jsonl etc.
python analyze.py
python badge/generate_badges.py
python site/build_site.py         # writes site/index.html
```

## Snapshot numbers (2026-09-29)

19,091 Bazaar listings across 2,033 hosts; 37,173 latest-version MCP Registry servers; 361 of 15,333 remote MCP servers (2.35%) match an x402 seller host by hostname. Median response time 1,118 ms in a sample of 2,038 hosts.

## Honest status and limitations

- The score is a heuristic. Its weights are motivated by published research on agent tool selection, but it has **not** been validated against real agent selection outcomes. Treat it as a hygiene indicator, not a ground truth.
- The LLM simulated-selection step was **not run** (no API key was available); `out/llm_simulated_selection.json` records the skip.
- The raw data snapshot (97 MB + 41 MB) and the full per-listing `scored_all.jsonl` are not in this repo (size); re-run the collector to reproduce. `build_site.py` needs `out/scored_all.jsonl`, so the site can only be rebuilt after a local run; `dist/` is the already-built output.
- Listings are third-party public data from the Bazaar and MCP Registry. If you are listed and want a correction or delisting, open an issue.
- The paid diagnosis product is not built; the site's button is a mailto request.

License: MIT.
