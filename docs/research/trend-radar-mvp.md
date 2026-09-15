# Trend Radar MVP for YouTube Shorts

This document proposes an MVP to collect country-specific trend data, score content opportunities, and generate briefs/scripts for Shorts and long-form videos.

## Goal

Build a lightweight system that answers this daily question:

> Which topics are trending in each target country, which ones are worth producing for monetization, and what short/video should we make today?

The MVP should not auto-publish content. It should produce ranked opportunities and scripts for human approval.

## Non-goals for the MVP

- No fully autonomous publishing.
- No scraping private or copyrighted channel content.
- No guaranteed RPM prediction.
- No investment, tax, medical, or legal advice generation without review.
- No reliance on only one trend source.

## Target countries for v1

Start small to keep signal quality high.

### Premium English markets

- US — United States.
- GB — United Kingdom.
- CA — Canada.
- AU — Australia.

### Premium Europe tests

- DE — Germany.
- NL — Netherlands.
- NO — Norway.
- DK — Denmark.
- CH — Switzerland.
- SE — Sweden.

### Spanish markets

- ES — Spain.
- MX — Mexico.
- CO — Colombia.
- CL — Chile.
- AR — Argentina.
- PE — Peru.

Optional later:

- BR — Brazil.
- FR — France.
- JP — Japan.

## Data sources

### 1. Google Trends RSS

Purpose: fast daily signal for currently trending searches by country.

Example endpoint:

```text
https://trends.google.com/trending/rss?geo=US
```

Pros:

- Free.
- Simple.
- Good enough for a first daily radar.
- No cloud setup required.

Cons:

- Unofficial/limited behavior.
- Can change without notice.
- Less historical depth and weaker metadata.

Estimated cost: **free**.

### 2. Google Trends BigQuery public dataset

Purpose: more structured Top 25 and Top 25 Rising queries, including daily country-level data and historical analysis.

Pros:

- Public Google dataset.
- Daily data.
- International coverage for many countries.
- Better for trend history and dashboards.

Cons:

- Requires Google Cloud / BigQuery setup.
- Not every country may be available.
- Scores are relative interest, not absolute search volume.
- Query costs can appear if usage exceeds free tier.

Estimated cost:

| Item | Cost expectation |
| --- | --- |
| BigQuery sandbox | Free, limited capabilities. |
| BigQuery free tier | Up to Google Cloud's current free query/storage limits. Verify before use. |
| Above free tier | Paid per data processed and storage. Keep partition filters to control cost. |

MVP recommendation: use RSS first, then BigQuery once the basic scoring works.

### 3. YouTube Data API

Purpose: enrich trend topics with YouTube search results, channel/video metadata, and competition indicators.

Potential uses:

- Search videos for a trend topic in a country/language.
- Pull title, channel, publish date, view count, like count, comment count.
- Estimate competition and format patterns.

Pros:

- Official API.
- Useful for YouTube-native validation.

Cons:

- Daily quota limits.
- Search calls are quota-expensive.
- View velocity requires repeated snapshots over time.
- API does not provide RPM or private analytics.

Estimated cost:

| Item | Cost expectation |
| --- | --- |
| Default quota | Usually free quota units per day, subject to Google project limits. |
| More quota | Quota increase request; not a normal pay-per-call product. |
| Cloud project | May require billing-enabled Google Cloud project depending on setup. |

### 4. Gemini or local LLM

Purpose: classify trends, generate briefs, summarize sources, and draft scripts.

Options:

| Option | Cost | Notes |
| --- | --- | --- |
| Gemini API | Has free/paid tiers depending on current Google AI Studio limits. Paid per token beyond free usage. | Good quality and easy integration. |
| Local LLM via Ollama/LM Studio/vLLM | No API cost; uses local hardware/electricity. | Lower marginal cost, more maintenance. |
| OpenRouter/other LLM APIs | Paid per token. | Useful fallback if Gemini limits are hit. |

### 5. Optional news/RSS sources

Purpose: validate why a trend exists and avoid making uninformed or risky content.

Potential sources:

- Google News RSS.
- Official government/statistics pages for country-specific data.
- Tech/product blogs.
- Finance calendars.
- Reddit/Hacker News for tech trend discovery.

Estimated cost: usually **free**, but source-specific rate limits apply.

### 6. n8n

Purpose: orchestration, approvals, notifications, and routing to content production.

Options:

| Option | Cost | Notes |
| --- | --- | --- |
| Self-hosted n8n | Free software; infrastructure cost only. | Best for local/private workflow. |
| n8n Cloud | Paid subscription. | Easier hosting and public webhooks. |
| Local n8n + tunnel | Free/low cost, but less robust. | Needed if Telegram/webhook approvals must reach a local machine. |

### 7. Telegram / Discord approval bot

Purpose: approve or reject suggested topics from a phone.

Estimated cost: **free** for normal usage.

### 8. Storage/database

MVP options:

| Option | Cost | Best for |
| --- | --- | --- |
| JSON files in repo/workdir | Free | First local MVP. |
| SQLite | Free | Local historical tracking. |
| Postgres | Free self-hosted; hosting costs if managed | Multi-user or dashboard future. |
| Google Sheets | Free limits | Simple manual review. |
| BigQuery tables | Free tier then paid | Analytics-heavy version. |

## MVP architecture

```text
Cron / manual run
  -> collect Google Trends RSS by country
  -> optionally collect BigQuery Trends
  -> normalize topics
  -> classify niche and language
  -> score opportunity
  -> generate daily report
  -> human approves topics
  -> generate script/brief
  -> send to OpenShorts / AI Shorts / manual recording workflow
```

## Suggested repository structure

```text
tools/trend-radar/
  README.md
  countries.yml
  niches.yml
  sources.yml
  collect_trends_rss.py
  collect_trends_bigquery.py
  enrich_youtube.py
  score_opportunities.py
  generate_briefs.py
  render_report.py
  data/
    raw/
    normalized/
    scored/
  reports/
```

## Data model

### Trend item

```json
{
  "id": "2026-09-15-US-ai-tax-deductions",
  "date": "2026-09-15",
  "country": "US",
  "language": "en",
  "source": "google_trends_rss",
  "query": "AI tax deductions",
  "rank": 3,
  "trend_score": 82,
  "related_queries": [],
  "source_urls": []
}
```

### Scored opportunity

```json
{
  "trend_id": "2026-09-15-US-ai-tax-deductions",
  "niche": "finance_ai",
  "country_value_weight": 1.0,
  "niche_monetization_weight": 0.95,
  "freshness_score": 0.9,
  "content_fit_score": 0.8,
  "risk_score": 0.35,
  "opportunity_score": 78,
  "recommended_format": "short_explainer",
  "needs_human_review": true
}
```

### Content brief

```json
{
  "title": "Freelancers are missing this AI tax deduction",
  "country": "US",
  "audience": "freelancers using AI tools",
  "hook": "If you paid for AI tools this year, this might matter at tax time.",
  "script_45s": "...",
  "long_form_outline": ["..."],
  "risk_notes": ["Tax advice risk; frame as educational and cite sources."],
  "sources": ["..."]
}
```

## Scoring model

Initial score:

```text
opportunity_score =
  trend_velocity
  * country_value_weight
  * niche_monetization_weight
  * content_fit_score
  * freshness_score
  * language_confidence
  - risk_penalty
```

### Country value weights

These weights are directional and should be replaced with channel analytics once available.

| Country | Weight |
| --- | ---: |
| US | 1.00 |
| AU | 0.95 |
| CA | 0.90 |
| GB | 0.88 |
| DE | 0.82 |
| NO | 0.80 |
| DK | 0.78 |
| CH | 0.78 |
| NL | 0.74 |
| SE | 0.72 |
| ES | 0.60 |
| MX | 0.45 |
| CL | 0.45 |
| CO | 0.40 |
| AR | 0.38 |
| PE | 0.35 |
| BR | 0.42 |
| JP | 0.70 |

### Niche weights

| Niche | Weight |
| --- | ---: |
| Finance / investing | 1.00 |
| AI tools / automation | 0.95 |
| SaaS / B2B | 0.95 |
| Career / jobs | 0.85 |
| Real estate / cost of living | 0.85 |
| Programming / tech career | 0.80 |
| Education / skills | 0.75 |
| Consumer tech | 0.65 |
| Health / fitness | 0.60 |
| News explainers | 0.55 |
| Entertainment | 0.30 |
| Sports | 0.25 |

## Risk classifier

Every topic should receive risk flags before script generation.

| Risk | Example | Action |
| --- | --- | --- |
| Copyright/reused content | clipping someone else's video | Block unless licensed/owned or strongly transformative. |
| Financial advice | stock pick, tax strategy | Add disclaimer, cite sources, avoid personalized advice. |
| Medical advice | diagnosis/treatment | Avoid or require strict sourcing/review. |
| Politics/crisis | elections, war, tragedy | Human review required. |
| Defamation/privacy | named individuals | Human review required. |
| Low monetization | memes, sports highlights | Allow only if strategic for reach. |

## Daily report format

```markdown
# Daily Shorts Opportunities — 2026-09-15

## Top opportunities

| Rank | Country | Topic | Niche | Score | Format |
| ---: | --- | --- | --- | ---: | --- |
| 1 | US | AI tax deductions | Finance + AI | 78 | 45s explainer |
| 2 | NO | Oslo rent prices | Cost of living | 72 | data explainer |

## US

### 1. AI tax deductions

- Score: 78
- Why now: Rising in Google Trends RSS.
- Audience: freelancers and small business owners.
- Hook: If you paid for AI tools this year, this might matter at tax time.
- Format: educational Short + long-form follow-up.
- Risk: tax advice; cite IRS/source and keep general.
```

## Integration with OpenShorts

### Clip Generator path

Use when the source is a long-form video owned by the channel.

Flow:

1. Trend Radar detects a topic.
2. Creator records or selects owned long-form video.
3. OpenShorts generates Shorts.
4. Human reviews clips.
5. Publish manually or through Upload-Post/n8n.

### AI Shorts path

Use when creating faceless or AI actor content from a trend brief.

Flow:

1. Trend Radar generates a brief/script.
2. AI Shorts creates actor/voice/b-roll/final video.
3. Human reviews for factual accuracy and claims.
4. Publish manually or through automation.

### Manual recording path

Use for higher trust niches like finance, taxes, jobs, immigration, and business.

Flow:

1. Trend Radar generates brief.
2. Human records short or long-form explanation.
3. OpenShorts clips the long-form version.

## Integration with n8n

Recommended MVP workflow:

```text
Cron daily
  -> Run Trend Radar script
  -> Send top 10 opportunities to Telegram/Discord
  -> Human approves 1-3 topics
  -> Generate content briefs/scripts
  -> Save to Google Sheets/Notion/local Markdown
  -> Optional: trigger OpenShorts or AI Shorts job
```

Do not auto-publish at MVP stage.

## Estimated service costs

| Service | Required for MVP? | Cost expectation | Notes |
| --- | --- | --- | --- |
| Google Trends RSS | Yes for v1 | Free | Unofficial/simple endpoint; monitor for changes. |
| Google Trends BigQuery | Optional v1, recommended v2 | Free tier/sandbox, then paid per query/storage | Use partition filters. Requires Google Cloud setup for serious use. |
| YouTube Data API | Optional v1, recommended v2 | Free quota-limited API; quota increase may be needed | Search calls consume quota quickly. |
| Gemini API | Optional but useful | Free/paid tiers depending on current Google AI Studio terms | Used for classification, briefs, scripts. |
| Local LLM | Optional alternative | No API cost; hardware/electricity cost | Good for classification and drafts once tuned. |
| n8n self-hosted | Optional | Free software; server cost if hosted | Good orchestrator. |
| n8n Cloud | Optional | Paid subscription | Easier public webhooks and reliability. |
| Telegram bot | Optional | Free | Good approval UI. |
| Google Sheets | Optional | Free limits | Simple queue/review board. |
| SQLite/JSON | Yes for local MVP | Free | Start here. |
| OpenShorts self-hosted | Optional production path | Free software; machine/API costs | Uses existing local stack. |
| Upload-Post | No for MVP | Free limited tier, then paid | Only needed for direct publishing/scheduling. |
| YouTube channel publishing manually | Yes possible | Free | Manual upload avoids publishing API complexity. |

## Build phases

### Phase 0: Manual spreadsheet

Duration: 1-2 days.

- Manually collect Google Trends RSS for 5 countries.
- Score in a spreadsheet.
- Generate 10 scripts manually with AI assistance.

Purpose: validate scoring categories before coding.

### Phase 1: Local CLI radar

Duration: 2-4 days.

- Python collector for Google Trends RSS.
- YAML country/niche configs.
- JSON/SQLite storage.
- Markdown report.
- Basic Gemini/local LLM classifier.

Deliverable:

```bash
python tools/trend-radar/render_report.py --date today
```

### Phase 2: YouTube enrichment

Duration: 3-5 days.

- Add YouTube Data API search enrichment.
- Store top competing videos for each topic.
- Estimate velocity by re-checking selected videos after 6/24 hours.

### Phase 3: n8n approval workflow

Duration: 2-4 days.

- Daily cron.
- Telegram/Discord approval.
- Approved briefs saved to Markdown/Sheets.
- Optional OpenShorts job trigger.

### Phase 4: Dashboard

Duration: later.

- Web UI in OpenShorts or separate local dashboard.
- Country/niche filters.
- Score history.
- Approved/rejected feedback loop.

## Success metrics

Operational metrics:

- Trends collected per country per day.
- Percentage classified successfully.
- Number of approved briefs per day.
- Time from trend detection to publish-ready asset.

Content metrics:

- Views after 1h, 6h, 24h, 7d.
- Average percentage viewed.
- Viewed vs swiped away.
- Subscribers per 1,000 views.
- Comments per 1,000 views.
- Long-form click-through if linked.
- Revenue per 1,000 views once monetized.
- Affiliate/product conversion where applicable.

Feedback loop:

- Increase score weight for countries/niches that produce subscribers and conversions.
- Decrease score weight for topics with low retention or high rejection rate.
- Keep a rejected-topic log to avoid repeating low-quality trends.

## Recommended first implementation

Start with:

- Countries: US, GB, CA, AU, DE, NO, ES, MX, CL, CO.
- Sources: Google Trends RSS only.
- Storage: SQLite or JSON files.
- LLM: Gemini if available; local LLM fallback later.
- Output: Markdown daily report.
- Publishing: manual only.

This creates useful daily intelligence without introducing API quota, BigQuery cost, or auto-publishing risk too early.
