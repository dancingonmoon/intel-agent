# intel-agent

Topic-driven daily intelligence reports from RSS, Telegram, news search, X and
Yandex News. Configure a topic and its sources, run it manually or on a timer,
and get a Markdown report with citations, event clustering and source-health
feedback.

The bundled topic is **Russian PMR / private networks**: TETRA, DMR/dPMR/NXDN/
P25, PoC/PTT, trunked radio, professional radios, dispatch communications and
Private LTE. The engine is generic; copy the topic YAML to track another market.

## Why it is different

- **Fact-only digest**: every URL, original title, source and date lives in
  `digest.json`; the summary may only reference item ids. The renderer resolves
  citations, so the model cannot invent links.
- **Source quality loop**: every run records source health; the report lists
  failed/degraded sources. `intel source audit` scores keep / watch / remove.
- **Feedback-driven learning**: mark an item or source good/bad; the next
  `intel source learn` shrinks weak sources and suggests validated new ones.
- **Manual source control**: add an RSS/Telegram source, test it, and keep the
  change in a small overlay file without editing the curated base YAML.
- **Readable output**: Chinese analysis plus English translations for titles in
  other languages; Chinese/English titles stay unchanged.
- **Degrades safely**: if the LLM is unavailable, it writes a deterministic
  template report and marks it degraded instead of failing delivery.

## Quick start

Requirements: Python 3.12, `curl`, and network access. X/Yandex need a normal
Chrome with remote debugging (optional).

```bash
git clone https://github.com/dancingonmoon/intel-agent.git
cd intel-agent
python3.12 -m venv .venv
.venv/bin/pip install -e .

intel init                 # creates config/settings.yaml from the example
$EDITOR config/settings.yaml
$EDITOR config/topics/ru-tetra.yaml

intel doctor
intel run --topic ru-tetra
```

`intel init` writes `config/settings.yaml` (gitignored) and an empty
`config/topics/ru-tetra.sources.yaml` overlay. The report is written to
`~/reports/intel/ru-tetra/YYYY-MM-DD.md` by default.

## Commands

| Command | Purpose |
|---|---|
| `intel init` | create local settings from the commented example |
| `intel doctor` | check proxy, Chrome/CDP, X/Yandex login, LLM, delivery |
| `intel run --topic <id>` | collect, summarize, render and deliver |
| `intel collect --topic <id>` | only collect a digest (debugging) |
| `intel source test --rss <url>` | fetch and validate an RSS candidate |
| `intel source test --telegram <channel>` | fetch a Telegram public preview |
| `intel source add --topic <id> --rss <url>` | validate and add to the overlay |
| `intel source disable --topic <id> --source <id>` | disable a source |
| `intel source list --topic <id>` | list configured/disabled sources |
| `intel source audit --topic <id>` | keep/watch/remove score table |
| `intel source suggest --topic <id>` | suggest feeds cited by selected items |
| `intel source learn --topic <id> --apply` | auto-disable weak sources, add validated trials |
| `intel feedback --topic <id> --item <id> --good` | record item feedback |
| `intel feedback --topic <id> --source <id> --bad` | record source feedback |
| `intel research --topic <id> --question "..."` | longer evidence-backed research report |

Use `--no-deliver` to write only the local Markdown, `--force-deliver` to resend
the same day, and `--dry-run` to print delivery payloads without sending.

## Configuration

- `config/settings.example.yaml` — runtime settings, fully commented.
- `config/topics/ru-tetra.yaml` — topic definition: facets, entities, queries,
  RSS and Telegram sources, exclusions.
- `config/topics/ru-tetra.sources.yaml` — user/learned overlay (gitignored).
  `intel source add/disable/learn` writes here; the base YAML comments are never
  overwritten.

Source additions and disables are merged at load time:

```yaml
add:
  rss:
    - https://example.com/feed.xml
  telegram_channels:
    - example_channel
disable:
  sources:
    - example.com
```

## Source quality and learning

Metrics combine fetch health, selected-item count and explicit feedback:

- `keep` — healthy and yielding.
- `watch` — flaky (captcha, selector drift, CDP), zero yield, or low success.
- `remove` — repeated total failure or repeated negative feedback without good.
- `expand` — selected repeatedly with positive feedback.

`intel source learn` is conservative: `--apply` only disables `remove` sources
and adds feeds that were actually fetched and parsed. New sources come from
domains/Telegram links cited by selected reports; they are validated before
being written to the overlay. See [docs/SOURCES.md](docs/SOURCES.md).

## Scheduling

Linux systemd user timer (recommended):

```bash
./scripts/install_timer.sh ru-tetra
systemctl --user list-timers 'intel-agent*'
systemctl --user start intel-agent@ru-tetra.service   # manual test
journalctl --user -u intel-agent@ru-tetra.service -f
```

Change the time in `systemd/intel-agent@.timer` (`OnCalendar=*-*-* 07:30:00`)
and rerun the installer. `RandomizedDelaySec=5m` adds jitter; set it to `0` for
an exact time. The service starts Chrome CDP if port 9222 is down.

Cron alternative:

```cron
30 7 * * * cd /path/to/intel-agent && .venv/bin/python -m intel.cli run --topic ru-tetra >> ~/.cache/intel-agent/cron.log 2>&1
```

## Delivery

Local Markdown is always written. Delivery adapters are optional:

- **Feishu**: imports Markdown as a native docx, sends an interactive Card 2.0
  with facet counts and a reading button, and recalls the previous card.
  Requires `lark-cli` with user authorization.
- **SMTP email**: set `delivery.email.mode: smtp`, host/user/to and the password
  environment variable.
- **WeChat/iLink**: optional personal adapter; disabled by default.
- **PDF/HTML**: off by default. Set `delivery.wechat.format: pdf|html` only when
  you need an archival copy; otherwise reports stay Markdown-only.

## LLM

`llm.provider: auto` resolves in this order:

1. `claude` CLI with a reachable gateway (`preflight_host:port`);
2. `ANTHROPIC_API_KEY` via the Messages API;
3. deterministic template (`none`).

Explicit providers are `claude-cli`, `anthropic`, and `none`. The LLM receives
the digest and returns Chinese prose plus English title translations; it never
writes URLs or original titles. If output is empty or violates the schema, the
agent falls back to the template and marks the report degraded.

## X and Yandex

Both use a normally-started Chrome with remote debugging, not a browser launched
by agent-browser. When agent-browser launches Chrome itself,
`navigator.webdriver` is `true` and x.com refuses to render.

```bash
./scripts/start_chrome_cdp.sh
.venv/bin/python scripts/x_login.py
.venv/bin/python scripts/yandex_login.py
```

The Chrome profile lives at `~/.local/state/intel-agent/chrome-profile`. Leave
it running; daily runs attach through CDP 9222. Yandex SmartCaptcha is manual; if
it appears, log in with `yandex_login.py` and solve it once.

## Project layout

```text
intel/            Python package (CLI, pipeline, renderer, delivery, learning)
config/           settings example, topics, glossary
scripts/          Chrome/X/Yandex login, timer install, cleanup, source audit
systemd/          portable service/timer templates
tests/            unittest suite
docs/             user guide and source guide
```

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Docs

- [Chinese user guide](docs/USER_GUIDE.zh-CN.md)
- [Source guide](docs/SOURCES.md)

## License

MIT. See [LICENSE](LICENSE).
