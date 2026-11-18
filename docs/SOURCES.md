# Sources: model, validation and learning

`intel-agent` treats sources as data, not code. A topic file declares where to
look; an overlay file records local additions and disables; SQLite records how
each source behaved. Nothing is learned into the curated topic file, so updates
to the bundled topic never conflict with local changes.

## 1. Source kinds

| Kind | ID form | Adapter | Notes |
|---|---|---|---|
| RSS/Atom | the feed URL | `sources/rss.py` | Titles, links, dates; also used for vendor newsrooms |
| Telegram | `telegram:@channel` | `sources/telegram.py` | Public preview (`t.me/s/<channel>`), no login |
| Google News | `google_news_ru`, `google_news_en` | `sources/google_news.py` | Locale-specific RSS |
| Bing News | `bing_news` | `sources/bing_news.py` | Redirect URLs are unwrapped |
| Brave | `brave` | `sources/brave.py` | Needs `BRAVE_SEARCH_API_KEY` |
| X | `x` | `sources/x.py` | Logged-in Chrome over CDP |
| Yandex News | `yandex` | `sources/yandex.py` | Logged-in Chrome; captcha is manual |
| GDELT | `gdelt` | `sources/gdelt.py` | Disabled in the bundled topic when the exit IP is rate-limited |

Engine priority and per-run query caps live in `engines.priority` and
`engines.per_engine_max_queries`.

## 2. Relevance pipeline

A raw article becomes a candidate only if it passes, in order:

1. **Window** — `published_at` inside `window.hours` (or unseen today when
   `include_seen` is false).
2. **Exclusions** — `exclude` terms and `exclude_sources` domains.
3. **Entity** — `entities.required_any` must appear in title+snippet.
4. **Title** — `entities.require_in_title` must appear in the title. This is
   what keeps generic upstream-electronics and foreign vendor pages out of a
   Russia-focused topic.
5. **Geo** — `entities.geo_any` must appear, unless the source is in
   `entities.source_any` or an entity in `entities.boost` is present.
6. **Context** — `entities.context_any` for search-engine results whose title
   may omit the standard name.
7. **Social context** — `entities.social_context_any` for X/Telegram, so a
   username or hashtag alone is not enough.
8. **Dedup** — canonical URL, then title similarity.

Matching is case-insensitive and word-boundary aware; phrases such as `PoC `
keep their trailing space so they do not match `Pocket`.

## 3. Where sources are declared

Sources are **topic-local**. A new topic gets its own file and does not inherit
another topic's queries, feeds or Telegram channels. Shared outlets (for
example ComNews) must be added to each topic that needs them; this keeps the
relevance rules and exclusions independent.

- `config/topics/<id>.yaml` — curated base. Keep it generic and publishable.
- `config/topics/<id>.sources.yaml` — local overlay (gitignored). Written by
  `intel source add/disable/enable/learn`.

Overlay schema:

```yaml
add:
  rss:
    - https://example.com/feed.xml
  telegram_channels:
    - example_channel
  queries: []          # reserved
disable:
  sources:
    - example.com
    - telegram:@example
```

Merging happens in `config.load_topic`; disables match either an exact source
ID or a domain substring.

## 4. Validating a candidate

```bash
intel source test --rss https://example.com/feed.xml
intel source test --telegram example_channel
```

A source is accepted only when the fetch succeeds **and** at least one item
parses. `intel source add` runs this automatically; `--force` skips it and is
not recommended for publication.

What "valid" does not mean: relevance. A working feed can still yield nothing
for your topic. That is what the health/selection metrics below are for.

## 5. Health and selection metrics

Every run writes one `source_health` row per attempted source: `ok`, item
count, error, duration. Every delivered report writes `report_selections` rows
linking `item_id` → the adapter that found it. `intel feedback` writes
`source_feedback`.

`intel source audit --topic <id> --runs 10` combines them over the last 30 days:

| Verdict | Rule |
|---|---|
| `remove` | 3+ runs with zero successes, or 3+ bad feedback and no good |
| `watch` | 8+ runs, items fetched but never selected |
| `watch` | success rate below 60% |
| `watch` | last error mentions selector drift, captcha or CDP |
| `expand` | 2+ selected items and at least one good feedback |
| `keep` | everything else |

`watch` is intentionally non-destructive: flaky browser adapters and quiet
vendors are normal, and a single captcha should not delete a source.

## 6. Learning

```bash
intel source learn --topic ru-tetra            # report only
intel source learn --topic ru-tetra --apply    # write the overlay
```

With `--apply`:

- `remove` sources are added to `disable.sources`;
- candidate feeds cited by selected reports are discovered from their domains
  and Telegram links, tried at common feed paths (`/rss`, `/feed`, ...), and
  added only if they parse;
- nothing else is changed.

Set `sources.learning.auto_apply: true` to run this after every successful
`intel run`. Default is `false`; enable it after a week of stable reports.

## 7. Manual feedback

```bash
intel feedback --topic ru-tetra --item it_0007 --good
intel feedback --topic ru-tetra --item it_0011 --bad --note "vendor PR, no Russia angle"
intel feedback --topic ru-tetra --source telegram:@channel --bad
```

Item feedback is attributed to the adapter recorded in `Item.found_by`, so it
affects exactly the source that surfaced the item.

## 8. Adding a new topic

1. Copy `config/topics/ru-tetra.yaml` to `config/topics/<id>.yaml`.
2. Set `id`, `name`, `timezone`, `languages`, `countries`.
3. Replace `facets`, `entities`, `exclude`, `queries`, `rss` and
   `telegram_channels`.
4. Keep `entities.require_in_title` narrow; it is the main precision control.
5. Run `intel collect --topic <id>` and inspect `digest.json` before spending
   LLM tokens.
6. Add one or two trusted specialist sources to `entities.source_any` so their
   articles are accepted even when the title omits the country.

## 9. Operational limits

- Telegram public previews expose only recent posts; high-volume channels can
  push older items out between runs.
- X and Yandex need a persistent Chrome profile. Closing it logs you out.
- Google News RSS is rate-limited per IP; keep `per_engine_max_queries` modest.
- A source that works from your laptop may fail from a server IP (captcha,
  geo-block). Treat health data as environment-specific.
