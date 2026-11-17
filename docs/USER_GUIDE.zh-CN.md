# intel-agent 操作使用文档（中文）

本文面向第一次使用 `intel-agent` 的用户，说明环境搭建、主题配置、信源管理、运行、定时和排错。

---

## 1. 产品是什么

`intel-agent` 是一个“主题 + 信源 + 定时 + 报告”的命令行工具：

1. 按主题从 RSS、Telegram 公开预览、Google/Bing News、Brave、X、Yandex News 采集；
2. 做相关性过滤、事件聚类、四维分类（重大合同 / 行业动态 / 技术发展 / 主要玩家）；
3. 生成中文 Markdown 报告，保留原文链接和来源；非中英文标题翻译成英文；
4. 可选投递到飞书或 SMTP 邮箱；
5. 记录每个信源的健康度，支持反馈驱动的信源增删。

默认主题是“俄罗斯专网通信（TETRA/DMR/PoC/PMR）”，但引擎是通用的。

---

## 2. 环境要求

必需：

- Linux / macOS（Windows 建议 WSL）
- Python 3.12
- `curl`
- 能访问目标新闻源；中国大陆通常需要本地代理

可选：

- Chrome / Chromium：X 与 Yandex News
- `lark-cli`：飞书文档和卡片投递
- `claude` CLI 或 `ANTHROPIC_API_KEY`：中文报告；没有时退化为规则模板

---

## 3. 安装

```bash
git clone https://github.com/dancingonmoon/intel-agent.git
cd intel-agent

python3.12 -m venv .venv
.venv/bin/pip install -e .

# 生成个人配置（不会进 Git）
intel init
```

如果 `intel` 不在 PATH，使用：

```bash
.venv/bin/python -m intel.cli --help
```

---

## 4. 第一次配置

### 4.1 运行配置

```bash
$EDITOR config/settings.yaml
```

重点字段：

```yaml
proxy:
  url: socks5h://127.0.0.1:10808   # 没有代理就留空

paths:
  reports_dir: ~/reports/intel
  state_dir: ~/.local/state/intel-agent

llm:
  provider: auto                    # auto / claude-cli / anthropic / none
  model: ""                         # anthropic 时可填 claude-sonnet-5-5

delivery:
  primary: local                    # local / feishu / all
  feishu:
    enabled: false                  # 用飞书时改 true
  email:
    enabled: false                  # 用 SMTP 时改 true
```

所有字段在 `config/settings.example.yaml` 中都有中文注释。

### 4.2 主题配置

`config/topics/ru-tetra.yaml` 决定“搜什么、从哪搜、排除什么”。主要字段：

- `window.hours`：回溯小时数，168 = 7 天
- `window.include_seen`：是否保留前几天已入库的条目
- `facets`：四维分类关键词
- `entities.required_any`：必须命中的主题词
- `entities.require_in_title`：标题必须命中的词（可为空）
- `entities.geo_any`：地理约束
- `entities.boost`：重点厂商/机构，命中后放宽地理限制
- `entities.source_any`：专业信源白名单
- `exclude` / `exclude_sources`：噪声词与噪声站点
- `queries`：各引擎查询、RSS、Telegram 频道

新增主题：复制 `config/topics/ru-tetra.yaml`，修改 `id`、`name`、`facets`、`entities`、`queries`。

### 4.3 环境变量

敏感信息不要写进 YAML。建议：

```bash
mkdir -p ~/.config/intel-agent
chmod 700 ~/.config/intel-agent
cat > ~/.config/intel-agent/env <<'EOF'
BRAVE_SEARCH_API_KEY=...
# ANTHROPIC_API_KEY=...
# SMTP_PASSWORD=...
# INTEL_ALERT_CMD=/path/to/your-alert-script
EOF
chmod 600 ~/.config/intel-agent/env
```

---

## 5. 检查环境

```bash
intel doctor
```

会检查代理、cc-switch/Anthropic、Chrome CDP、X/Yandex 登录标记、投递目录等。`WARN` 不一定是阻塞项，但先修 `FAIL`。

---

## 6. 信源管理

### 6.1 先测试，再加入

```bash
# RSS
intel source test --rss https://example.com/feed.xml

# Telegram 公开频道
intel source test --telegram example_channel
```

成功会显示 `ok: true` 和抓到的条目数。再加入：

```bash
intel source add --topic ru-tetra --rss https://example.com/feed.xml
intel source add --topic ru-tetra --telegram example_channel
```

默认会先实抓验证；`--force` 可跳过验证，但不建议。

### 6.2 查看、禁用、恢复

```bash
intel source list --topic ru-tetra
intel source disable --topic ru-tetra --source example.com
intel source enable  --topic ru-tetra --source example.com
```

信源 ID 的写法：

- RSS：配置里的 URL，例如 `https://example.com/feed.xml`
- Telegram：`telegram:@channel`
- 搜索引擎：`x`、`yandex`、`brave`

### 6.3 审计

```bash
intel source audit --topic ru-tetra --runs 10
```

输出 `keep / watch / remove / expand`：

- `keep`：健康且有产出
- `watch`：抓取不稳、验证码、selector drift、长期 0 条
- `remove`：连续多轮完全失败，或负面反馈远多于正面
- `expand`：多次入选且有正向反馈

### 6.4 反馈

```bash
# 对某条报告条目反馈
intel feedback --topic ru-tetra --item it_0001 --good
intel feedback --topic ru-tetra --item it_0001 --bad --note "不相关"

# 直接对某个信源反馈
intel feedback --topic ru-tetra --source telegram:@telecommunal --good
```

反馈写入 SQLite，下一轮审计/学习会使用。

### 6.5 自动学习

```bash
# 只看建议，不改配置
intel source learn --topic ru-tetra

# 自动禁用 remove 源，并加入已验证的候选源
intel source learn --topic ru-tetra --apply
```

自动加入的候选来自“已入选报告引用过的域名/Telegram 链接”，并且必须先通过 RSS/Telegram 实抓验证。配置项：

```yaml
sources:
  learning:
    enabled: true
    auto_apply: false   # true = 每次运行后自动执行
    runs: 10
```

---

## 7. 运行报告

### 7.1 手动运行

```bash
# 先启动 Chrome（X/Yandex 需要）
./scripts/start_chrome_cdp.sh

# 采集 + 总结 + 渲染 + 投递
intel run --topic ru-tetra
```

常用参数：

```bash
intel run --topic ru-tetra --no-deliver        # 只写本地 Markdown
intel run --topic ru-tetra --force-deliver     # 同一天重新投递
intel run --topic ru-tetra --dry-run           # 只打印投递 payload
intel run --topic ru-tetra --window-hours 24   # 临时改窗口
```

报告路径：

```text
~/reports/intel/<topic>/YYYY-MM-DD.md
```

默认只生成 Markdown。只有显式把 `delivery.wechat.format` 设为 `pdf` 或 `html` 时才会生成对应文件；历史文件可用：

```bash
./scripts/clean_reports.sh          # dry-run，列出 PDF/HTML
./scripts/clean_reports.sh --yes    # 确认后删除
```

### 7.2 只采集不总结

```bash
intel collect --topic ru-tetra --out /tmp/digest.json
```

用于检查信源健康度；不会调用 LLM。

---

## 8. 定时运行

### 8.1 systemd（推荐）

```bash
./scripts/install_timer.sh ru-tetra
systemctl --user list-timers 'intel-agent*'
systemctl --user start intel-agent@ru-tetra.service   # 手工触发一次
journalctl --user -u intel-agent@ru-tetra.service -f
```

改时间：

```bash
$EDITOR systemd/intel-agent@.timer
# OnCalendar=*-*-* 07:30:00
./scripts/install_timer.sh ru-tetra
```

`RandomizedDelaySec=5m` 会加最多 5 分钟抖动；要准点改成 `0`。

### 8.2 cron

```cron
30 7 * * * cd /path/to/intel-agent && .venv/bin/python -m intel.cli run --topic ru-tetra >> ~/.cache/intel-agent/cron.log 2>&1
```

cron 不会自动启动 Chrome，X/Yandex 可能降级；可在命令前加 `./scripts/start_chrome_cdp.sh &&`。

---

## 9. 投递

### 9.1 本地

始终启用，写 Markdown。

### 9.2 飞书

1. 安装并登录 `lark-cli`：
   ```bash
   lark-cli auth login
   lark-cli auth status --json
   ```
2. 配置：
   ```yaml
   delivery:
     primary: feishu
     feishu:
       enabled: true
       as: bot
       open_id: ""        # 留空自动读取
       recall_previous: true
   ```
3. 运行。飞书会导入 docx 并发送 Card 2.0；新卡片会自动撤回同主题上一张卡片。

飞书用户身份过期时，`drive +import` 会报 `need_user_authorization`，重新 `lark-cli auth login`。

### 9.3 SMTP 邮箱

```yaml
delivery:
  email:
    enabled: true
    mode: smtp
    host: smtp.example.com
    port: 587
    user: you@example.com
    password_env: SMTP_PASSWORD
    tls: true
    to: you@example.com
```

密码放在环境变量 `SMTP_PASSWORD`，不要写进 YAML。

### 9.4 微信/iLink

个人适配器，默认关闭；需要 iLink/ClawBot 环境。默认文本投递，不生成 PDF。

---

## 10. X 与 Yandex

两者都需要“正常启动的 Chrome + CDP”：

```bash
./scripts/start_chrome_cdp.sh
.venv/bin/python scripts/x_login.py
.venv/bin/python scripts/yandex_login.py
```

- X 查询尽量用简单词（如 `Hytera`、`LEO Telecom`），过于复杂的布尔/日期查询可能被 X 拒绝。
- Yandex 使用 News 端点；出现 SmartCaptcha 时手动登录并解一次。
- 登录状态保存在 `~/.local/state/intel-agent/chrome-profile`，不要删除。

---

## 11. 报告内容规则

- 事实只来自 `digest.json`；摘要只能引用 `item_id`。
- 中文摘要在 `summary_zh`；判断在 `why_it_matters`。
- 非中文/英文标题由 LLM 给英文翻译（`title_en`）；中文/英文标题保持不变。
- 最终展示字段不允许出现俄文/西里尔字符；人名公司名用英文转写。
- LLM 失败时输出确定性模板，报告标记 `degraded`，仍会投递。

---

## 12. 常见问题

**`intel doctor` 报 proxy 失败**

启动本地代理，或把 `proxy.url` 留空直连。

**X 报 selector drift / login required**

先 `./scripts/start_chrome_cdp.sh`，再 `scripts/x_login.py`；确认 `navigator.webdriver=false`。X 查询太复杂时改用简单词。

**Yandex 报 captcha**

运行 `scripts/yandex_login.py` 手动登录并解验证码；换代理节点。

**飞书 `need_user_authorization`**

`lark-cli auth login` 重新授权用户身份；只登录 bot 不够。

**报告是俄文**

确认 `llm.provider` 不是 `none`，且模型正常；渲染器会用 `title_en` 和西里尔转写兜底。

**信源一直 0 条**

`intel source audit` 看是否 `watch/remove`；用 `intel source test` 验证端点；必要时换 RSS/频道。

**定时器没跑**

`systemctl --user list-timers`、`journalctl --user -u intel-agent@ru-tetra.service`；确认 Chrome 和代理在运行。

---

## 13. 安全建议

- `config/settings.yaml`、`config/topics/*.sources.yaml` 和 `~/.config/intel-agent/env` 不进 Git。
- 不要把 API key、SMTP 密码、飞书 token 写进主题 YAML。
- 公开仓库只提交 `settings.example.yaml` 和脱敏后的主题配置。
