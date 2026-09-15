# Ad-translation pipeline — research (2026-09-11)

## Q1 — Meta Ad Library API (graph.facebook.com/ads_archive)

Source: https://www.facebook.com/ads/library/api/ (official), https://developers.facebook.com/docs/graph-api/reference/ads_archive/, https://apidog.com/blog/facebook-ad-library-api/, https://admanage.ai/blog/facebook-ads-library-api (2026 guide), https://swipekit.app/articles/meta-ad-library-api (2026), https://adlibrary.com/posts/meta-ad-library-free-api-2026

- Endpoint: `GET https://graph.facebook.com/v23.0/ads_archive` (current version as of 2026 blog examples; version string moves, endpoint stable).
- Covers **all EU ads under DSA transparency**, not just political — the API pulls from the same Ad Library that now legally requires DSA disclosure for all EU-run ads. Confirmed by 2026 guides discussing "increased transparency fields" from Brazil/EU regulation.
- Lookup by `ad_archive_id`/`id`: supported via `search_terms` or filtering; you can also query the ad directly by ID through `ad_snapshot_url` construction or a targeted `id` filter (community reports of direct ID lookup working; official docs emphasize `search_terms`-based search rather than a clean single-ID GET, so a search-then-filter pattern is the reliable path).
- Fields available for EU/non-political ads: `id`, `ad_creation_time`, `ad_creative_bodies` (primary text), `ad_creative_link_titles` (headline), `ad_creative_link_captions`, `ad_creative_link_descriptions` (description), `ad_snapshot_url`, `page_name`, `page_id`, `publisher_platforms`, `languages`, `ad_delivery_start_time`/`stop_time`. Political-only fields (spend, impressions, demographics) are gated to `ad_type=POLITICAL_AND_ISSUE_ADS` and won't apply to this ecommerce ad.
- **No direct downloadable image/video URL.** `ad_snapshot_url` returns a link to an HTML render page (`facebook.com/ads/archive/render_ad/?id=...`) showing uncompressed creative in an iframe — not a raw media URL in the JSON. You must fetch that page and extract the image/video src (screenshot or DOM-scrape), which several 2026 sources explicitly flag as "not officially supported for bulk, script it yourself." (source: apidog.com, admanage.ai, adlibrary.com — all confirm this same limitation independently.)
- Access: requires a Meta developer app + **identity verification** for anyone querying political/issue ads; for pure commercial/non-political EU ads under DSA transparency, verification requirements are lighter but an app + access token is still mandatory. No paid tier — it's free but rate-limited.

**Verdict: usable for text fields (primary text, headline, description) directly from the API. Image/video still needs a scrape of the `ad_snapshot_url` page.**

## Q2 — Scraping fallback + TrendTrack

- **Apify actors** (multiple, all live 2026):
  - `apify/facebook-ads-scraper` (official Apify) — $3.40–$5.80/1,000 ads depending on plan.
  - `curious_coder/facebook-ads-library-scraper` — $0.75/1,000 ads, 40K users, 4.8 rating — cheapest reputable option.
  - `api_creators/facebook-ads-library-scraper-api` — $1/1,000 ads, exports JSON/CSV/Excel, extracts creative + copy by Page ID or keyword.
  Source: apify.com store listings, pulled 2026-09-11.
- **ScrapeCreators** (scrapecreators.com/facebookAdLibrary-api) — REST API, cursor pagination, $47/25,000 credits (~$0.0019/credit), 100 free credits to start. Positioned as dev-friendly direct API vs Apify's actor model. Source: scrapecreators.com/blog/meta-ad-library-scraping (2026 comparison of 6 scrapers).
- **TrendTrack** (trendtrack.io) is an ecommerce ad-spy / dropshipping product-research tool (Shopify store data + winning-ad discovery), not primarily a Meta Ad Library scraper API. Its own blog markets an MCP integration ("AI-powered research capabilities... via MCP") for chat-based queries, but no evidence of a public REST export/API for pulling a specific ad by URL — it's a SaaS dashboard product. The sheet's "Trendtrack Ad Link" column is almost certainly just a link back to TrendTrack's own UI for that ad, not a data source to hit programmatically. Treat it as a **human reference link only**, not a pipeline input.
- Playwright direct scrape of the Ad Library page (or the render_ad iframe) is the free/no-cost option and is what several of the above services do under the hood; more brittle (Meta changes DOM), but zero marginal cost and full control over image extraction.

**Verdict: use the official ads_archive API for text fields, then either (a) Playwright-screenshot/scrape `ad_snapshot_url` for the image, or (b) use `curious_coder/facebook-ads-library-scraper` on Apify ($0.75/1K) if the image extraction needs to be robust and low-maintenance.**

## Q3 — Image text translation preserving layout/product

Source: ai.google.dev/gemini-api/docs/image-generation, kie.ai/nano-banana, nanobananaapi.ai, fal.ai/models/fal-ai/flux-pro/kontext, higgsfield.ai/blog/edit-photos-with-ai-higgsfield, OpenAI developers.openai.com/api/docs/pricing

| Tool | API? | Text-swap capability | Price/image |
|---|---|---|---|
| **Gemini "Nano Banana" (2.5 Flash Image / Gemini 3 Pro Image)** | Yes — `generativelanguage.googleapis.com` `interactions` endpoint, image+text in, image out | Purpose-built for exactly this: localized text-in-image editing while preserving photo/layout is a headline use case Google markets directly | 2.5 Flash: ~$0.02–0.039/image. Gemini 3 Pro (Nano Banana Pro): $0.139 (2K) / $0.24 (4K) official; ~20% cheaper via resellers like kie.ai (~$0.12 at 4K) |
| **Higgsfield "Edit Text"** | Product exists (higgsfield.ai/blog/edit-photos-with-ai-higgsfield) with an explicit "Edit Text" tool: "Finds text on an image and regenerates it with your replacement" — exactly the ask | Consumer web app first; API access not clearly documented in what surfaced — likely credit-based subscription, not a clean per-call API. Needs a direct check before committing to it for automation. |
| **Flux Kontext (Black Forest Labs)** | Yes, via fal.ai, Together, PiAPI, BFL direct | Good at localized edits with instruction prompts ("replace X text with Y"); reasonable but not marketed as text-in-image specialist the way Nano Banana is | $0.04/image (fal.ai pro tier) |
| **gpt-image-1 (OpenAI)** | Yes, `/v1/images/edits` | Capable general editor; text rendering fidelity historically weaker than Nano Banana for precise in-context text replacement | ~$0.01 (low) / $0.04 (medium) / $0.17 (high) per square image |
| Ideogram, Recraft | Not directly checked this pass | Both known for strong text rendering in generation, less proven specifically for edit-preserve-photo workflows | not priced this pass |

**Verdict: Gemini Nano Banana (2.5 Flash Image for speed/cost, Pro for quality) is the strongest fit — it's the model Google explicitly positions for "find and replace text in an image while keeping everything else," has a real API, and is cheapest at ~$0.02–0.04/image. Higgsfield is the client's own reference point and has the matching feature by name but its API story is unconfirmed — verify before betting the build on it. Flux Kontext is the solid fallback/alternative at $0.04/image with a mature API via fal.ai.**

## Q4 — Orchestration

- **n8n**: native Google Sheets trigger (new row) + native Google Drive node + HTTP Request nodes for Meta API/Apify/Gemini/Flux — all first-class, no custom auth plumbing needed beyond OAuth setup once.
- **Make**: same coverage, similar ease, slightly pricier at volume and less commonly self-hosted.
- **Plain Python**: full control, cheapest to run, but you own retry/logging/scheduling and it's not something a non-technical client can see running or tweak.

**Recommendation: n8n.** It gives a client-visible, editable workflow (they can see each ad moving through the pipeline live, which sells the demo) and every step needed — Sheets trigger, HTTP calls to the ad-library source, translation API, image-edit API, Drive upload, Sheets write-back — is a native node, so build time stays low and the client can hand you a new sheet row or column without touching code.

## Q5 — Legal/policy risk (competitor-ad translation)

Pulling text/creative from the Meta Ad Library is explicitly permitted by Meta's own terms for "analysis" purposes (ad_snapshot_url field description: "you can download ad creative such as images and text for an individual ad... it must be for analysis and you must comply with the data storage terms"). That covers research/benchmarking use. Re-publishing a translated near-copy of another advertiser's creative as your own ad, however, moves past "analysis" into reproducing a third party's copyrighted image/copy and branded content — the ad's photography, layout and any trademarked elements remain the original advertiser's IP regardless of translation, and running it as a paid ad under a different account risks a copyright/trademark takedown from the original advertiser (via Meta's IP-report tool) or a platform policy strike for deceptive/impersonation-adjacent creative, separate from any DSA/Ad-Library terms issue. This is a factual risk flag, not a blocker — advertising agencies routinely produce "inspired by" creative from competitor research, but a 1:1 translated clone of someone else's photo and ad copy is the highest-risk version of that practice and should get a legal/client sign-off before the pipeline auto-publishes anything, not just before it drafts it.
