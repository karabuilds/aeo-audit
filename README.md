# aeo-audit

[![CI](https://github.com/karabuilds/aeo-audit/actions/workflows/ci.yml/badge.svg)](https://github.com/karabuilds/aeo-audit/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

**A command-line tool that measures how often a company appears in AI-generated answers, compares it with competitors, and turns the results into a one-page report a marketing leader can act on.**

> **Status: in active development.** The measurement engine is built and tested. Question generation, findings, the one-page report and a sample audit are next. See [Roadmap](#roadmap).

## The problem

Buyers now ask ChatGPT, Claude, Gemini and Perplexity questions like "what is the best no-fee banking app?" before they ever visit a website. Brands cannot see how those answers describe them, or whether they show up at all. Answer engine optimization (AEO) starts with measuring that visibility, and most teams have no repeatable way to do it.

## What aeo-audit measures

aeo-audit asks each AI engine a set of real buyer questions, with live web search turned on, and records every answer. For the company and each competitor, it reports:

| Metric | What it tells you |
|---|---|
| **Mention rate** | How often the brand appears in answers |
| **Share of voice** | The brand's share of all tracked-brand mentions |
| **Average position** | Whether the brand is named first, second or later |
| **Owned citation rate** | How often answers cite the brand's own website |
| **Top cited third-party sites** | The review sites, publishers and forums AI engines rely on. These are where content gaps live. |
| **Gap list** | Questions where a competitor appears in most answers and the company appears in none |
| **By intent and topic** | Visibility for discovery, problem, comparison, use case and evaluation questions |
| **Consistency** | How stable the company's appearance is from one run to the next |

### Why it reports ranges

AI answers change between runs, even for the same question. A single number would overstate certainty. aeo-audit asks every question several times on every engine and reports each headline metric as an average with its low and high across runs, for example "40% (35% to 45%)".

## How it works

```
company file ──> buyer questions ──> human review ──> run on 4 engines ──> analysis ──> metrics.json ──> report
                 (no brand names)    (required)       (web search on)     (mentions,                    (one page,
                                                                           citations)                    plus outreach)
```

1. **Company file.** A short YAML file names the company, its aliases, category, target buyer and 2 to 3 competitors.
2. **Buyer questions.** The tool writes unbranded questions a real buyer would ask, spread across five intents and optional topic weights. An automated check rejects any question that contains a tracked brand name.
3. **Human review.** The tool stops and waits. Questions only run after a person approves them.
4. **Runs.** Each question runs several times on each engine as an independent call with no shared history.
5. **Analysis.** The tool finds brand mentions and cited sources in every answer.
6. **Metrics.** Every number goes into `metrics.json`, the single source of truth.
7. **Report.** A one-page HTML and PDF report, plus a short outreach message. An automated check confirms that every number in the text exists in `metrics.json`.

## Engines

| Engine | Model (default) | Search |
|---|---|---|
| OpenAI | `gpt-6-luna` | Responses API web search |
| Anthropic Claude | `claude-haiku-5-5` | Web search tool |
| Google Gemini | `gemini-3.8-flash` | Grounding with Google Search |
| Perplexity | `perplexity/sonar` | Agent API web search |

Model IDs, API parameters and prices live in [`config/engines.yaml`](config/engines.yaml), each with the date it was verified against the provider's official documentation. Nothing is hardcoded. An engine without an API key is skipped and listed as "not tested". One missing key never stops a run.

## Built for accuracy and safety

- **Brand matching that resists false positives.** Some brand names are ordinary words, like "Chime" or "Cash App". For those names, a match only counts with supporting context: the brand's capitalization plus a nearby product term, a mention of its domain, or a yes/no check by a low-cost classifier model. Every classifier decision is logged for review.
- **Clean citation data.** URLs are normalized (tracking tags removed, hosts lowercased, "www." dropped), reduced to their registrable domain, and labeled as owned, competitor or third party.
- **Cost control.** The tool shows a cost estimate and asks for confirmation before any paid call. A budget cap (default $5) blocks runs whose estimate is too high and stops a run if actual spending reaches it. `--dry-run` shows the plan without making calls.
- **Resumable runs.** Every answer is saved as it arrives. An interrupted run resumes and only makes the missing calls.
- **Reliable calls.** Each engine has its own concurrency limit, request timeout, and retries with exponential backoff and jitter for rate limits and server errors. Other client errors are not retried.
- **Private by default.** API keys load only from a local `.env` file and are never printed. Real company files and results are excluded from Git, so only a fictional sample is ever published.
- **Mock mode.** The full pipeline runs on saved sample answers, with no keys and no cost. Mock mode only works for the fictional sample company.

## Quick start

> The report commands are still in development. The steps below work today.

```bash
git clone https://github.com/karabuilds/aeo-audit.git
cd aeo-audit
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Create a company file to fill in:

```bash
aeo-audit init acme-treasury
```

Preview the plan and cost for a run without making any calls:

```bash
aeo-audit run acme-treasury --approve --dry-run
```

Run the test suite:

```bash
pytest
```

For real runs, copy `.env.example` to `.env` and add the API keys for the engines you want to test.

## Methodology and limitations

- **Successful calls only.** Failed calls are excluded from every metric, then counted and reported in the methodology section.
- **Mention rate range.** For each run number, the tool computes the share of answers that mention the brand. The headline shows the average of those values, plus the lowest and highest.
- **Share of voice.** Each brand counts at most once per answer.
- **Gaps.** A question is a gap when at least one competitor appears in more than half of its answers (all engines and runs combined) and the company appears in none.

Limitations, stated plainly:

- API results can differ from consumer apps, which may personalize answers or use different models and search settings.
- The default models are each provider's lower-cost options, chosen to keep a full audit at about $5 or less.
- Google AI Overviews is not covered.
- Results are a point-in-time snapshot and vary between runs.

## Roadmap

- [x] Company file validation with plain-language errors
- [x] Brand mention detection with a common-word guard and audit log
- [x] Citation extraction, normalization and classification
- [x] Metrics with run-to-run ranges, gaps, intent and topic breakdowns
- [x] Engine adapters for OpenAI, Anthropic, Gemini and Perplexity
- [x] Runner with caching, resume, retries, cost estimates and a budget cap
- [x] Buyer question generation with brand-name checks and human approval
- [ ] Findings and quick wins, with automated number validation
- [ ] Writing-rule checks that fail the build on violations
- [ ] One-page HTML and PDF report, plus outreach message
- [ ] Sample audit for a fictional company, with screenshot

## How I built this

I designed aeo-audit and built it with [Claude Code](https://claude.com/claude-code), working in stages: plan, build, test, review. I set the requirements and methodology and reviewed every stage's test results before moving on. Each provider's current documentation and pricing was checked before any integration code was written. The test suite and type checks run on every push.

## Author

**Kara Hartnett**
[LinkedIn](https://www.linkedin.com/in/kara-hartnett/)

## License

[MIT](LICENSE)
