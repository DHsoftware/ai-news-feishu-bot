# AI News Feishu Bot (Hybrid Architecture)

## 1. 项目说明
本项目采用两阶段混合架构：

A. GitHub Actions（公网侧）
- 抓取 AI 科技新闻 RSS 候选。
- 抓取 Codex Agent 学习资源候选（YouTube RSS、OpenAI Developers/Docs、Google News 教程搜索）。
- 生成 JSON 缓存：
  - `data/news-candidates/YYYY-MM-DD.json`
  - `data/learning-candidates/YYYY-MM-DD.json`
- 不调用公司 LiteLLM。
- 不调用飞书。
- 不保存公司密钥。

B. 本地电脑（公司内网侧）
- 先 `git pull` 拉取最新候选 JSON。
- 读取候选 JSON，调用公司 LiteLLM 生成日报。
- 飞书只发送 1 条消息（不拆 2/3、3/3）。
- 本地脚本仅使用 Python 标准库。

## 2. 安全边界
GitHub Actions 不使用、不保存以下配置：
- `LITELLM_API_KEY`
- `OPENAI_API_KEY`
- `FEISHU_WEBHOOK_URL`
- `FEISHU_BOT_SECRET`

上述配置只允许放在本地 `.env`。

## 3. 项目结构
```text
ai-news-feishu-bot/
  .github/
    workflows/
      collect-rss.yml
  data/
    news-candidates/
      YYYY-MM-DD.json
    learning-candidates/
      YYYY-MM-DD.json
  scripts/
    collect_rss.py
    daily_ai_news.py
  .env.example
  requirements.txt
  run_daily_ai_news.ps1
  run_daily_ai_news.sh
  README.md
  .gitignore
```

## 4. 本地运行前置条件
- Python 3.10+。
- 本机可访问：
1. GitHub 仓库（用于 `git pull`）
2. 公司 LiteLLM 网关
3. 飞书机器人 Webhook
- 如需 VPN 才能访问 LiteLLM，定时任务时 VPN 必须在线。
- 电脑睡眠或关机时，定时任务不会执行。

## 5. 本地 `.env` 配置
复制并填写：

```bash
cp .env.example .env
```

必填：
- `LITELLM_API_KEY`（推荐）
- `LITELLM_BASE_URL`（如 `http://10.36.244.180:30015/v1`）
- `LITELLM_MODEL`（默认 `gpt-5.4`）
- `FEISHU_WEBHOOK_URL`

选填：
- `FEISHU_BOT_SECRET`
- `TIMEZONE`（默认 `Asia/Shanghai`）
- `NEWS_MAX_CHARS`（默认 `3500`）
- `NEWS_TOP_N`（默认 `5`）
- `LITELLM_TIMEOUT_SECONDS`（默认 `90`）
- `LITELLM_RETRY_TIMEOUT_SECONDS`（默认 `120`）
- `LITELLM_RETRY_COUNT`（默认 `1`）

说明：
- 代码优先读取 `LITELLM_API_KEY`，兼容 `OPENAI_API_KEY`。
- `TIMEZONE`、`NEWS_MAX_CHARS`、`NEWS_TOP_N` 不填也可运行。
- 如果 LiteLLM 偶发 `timed out`，第一次请求默认等待 `90` 秒；`LITELLM_RETRY_COUNT=1` 表示超时或网络错误后重试 1 次，重试默认等待 `120` 秒。重试后仍失败时，脚本停止本次发送，不向飞书推送失败日报。
- `FEISHU_BOT_SECRET` 是飞书机器人“签名校验”密钥，不是 Webhook URL token。
- 若未开启签名校验，`FEISHU_BOT_SECRET` 留空。
- 不要提交 `.env`。

## 6. GitHub Actions 阶段（抓取公开元数据）
工作流：`.github/workflows/collect-rss.yml`

触发：
- 每天 07:50（Asia/Shanghai，对应 UTC 23:50）。
- 手动触发 `workflow_dispatch`。

执行：
- 运行 `python scripts/collect_rss.py`。
- 自动更新并提交：
  - `data/news-candidates/`
  - `data/learning-candidates/`

## 7. 学习资源抓取说明
`collect_rss.py` 会收集 Codex 学习资源候选，字段包括：
- `title`
- `summary`
- `summary_source`（如 `rss_description` / `rss_content_encoded` / `atom_summary` / `atom_content` / `page_meta_description` / `page_og_description` / `page_title` / `empty`）
- `summary_quality`（`high` / `medium` / `low` / `empty`）
- `published_at`
- `source`
- `source_type`（`official_doc` / `official_video` / `official_blog` / `github_repo` / `technical_blog` / `media_article` / `google_news`）
- `source_quality`（`high` / `medium` / `low`）
- `is_official_source`
- `language`
- `region`
- `link`
- `tags`

限制说明：
- YouTube RSS 只能拿到标题、链接、发布时间、简介等元数据。
- 不等于拿到完整字幕或完整视频内容。
- 因此“Codex Agent 每日一学”只会基于元数据提炼学习建议，并附原链接。
- Codex Agent 每日一学优先使用官方文档、官方视频和高质量技术博客。
- Google News 仅作为发现线索，不作为高可信学习内容来源；如果没有高质量学习资源，日报会提示“今日未发现高质量 Codex Agent 学习资源”。
- 对 AGENTS.md 的解释应限于项目说明、构建测试命令、代码风格和安全约束，不应过度解释为组织角色管理文件。
- 新闻和学习候选会尽量从 RSS/Atom 摘要、`content:encoded`、页面 meta description / og description 中提取轻量摘要；如果摘要为空或很短，会写入 `summary_quality=low/empty`，日报 prompt 会要求模型不要过度推断。

## 8. 如何新增 YouTube 频道/播放列表 RSS
在 `scripts/collect_rss.py` 的 `YOUTUBE_LEARNING_FEEDS` 中新增条目。

频道 RSS：
- `https://www.youtube.com/feeds/videos.xml?channel_id=<CHANNEL_ID>`

播放列表 RSS：
- `https://www.youtube.com/feeds/videos.xml?playlist_id=<PLAYLIST_ID>`

步骤：
1. 打开 YouTube 频道或播放列表页面。
2. 获取 `channel_id` 或 `playlist_id`。
3. 组装 RSS URL。
4. 加入 `YOUTUBE_LEARNING_FEEDS`。

## 9. 如何新增中文教程搜索关键词
在 `scripts/collect_rss.py` 中扩展 `LEARNING_GNEWS_QUERIES_ZH`，例如：
- `Codex CLI 进阶`
- `Codex Agent 工作流 实战`
- `Codex MCP 集成`

## 10. 本地日报脚本行为
入口：`scripts/daily_ai_news.py`

流程：
1. 读取 `data/news-candidates/YYYY-MM-DD.json`，优先使用 `curated_items`；如果目标日期新闻 JSON 缺失则静默退出，不回退旧新闻。
2. 读取 `data/learning-candidates/YYYY-MM-DD.json`，优先使用 learning `curated_items`，缺失时回退最近缓存。
3. 从 learning candidates 选择 1 条“Codex Agent 每日一学”资源（优先官方 Codex > YouTube > 教程）。
4. 调用 LiteLLM：`POST {LITELLM_BASE_URL}/chat/completions`。
5. 解析严格 JSON（`summary`、`top_news`、`codex_learning`）。
6. 飞书发送仅 1 条消息（interactive 失败降级 post，再降级 text）。

如果目标日期的新闻 candidates JSON 不存在，通常表示本地 `git pull` 没有拉到新的日报候选文件；脚本会静默退出，不调用 LiteLLM，也不推送飞书，避免重复发送旧日报。

飞书消息结构：
1. 今日摘要
2. 重要新闻 Top 3
3. Codex Agent 每日一学

如果学习资源为空：
- 第三部分显示“今日未发现新的 Codex Agent 学习资源。”

## 10.1 RSS 前端策展与去重
`collect_rss.py` 现在会在 GitHub Actions 阶段完成新闻候选清洗，减少 LiteLLM 输入噪声：
- RSS 抓取与来源分组
- 当天内部标题/链接去重
- 跨天历史去重
- 弱相关过滤
- 主题打标与分类
- 新闻评分
- 全球/中国/中国汽车新闻平衡筛选
- 生成 `curated_items`

新闻 JSON 会保留：
- `curated_items`：默认 5 条，供 `daily_ai_news.py` 直接传给 LiteLLM。
- `selection_config`：本次筛选配置。
- `history_dedupe`：历史去重统计。
- `rejected_stats` / `rejected_samples`：被过滤原因与样例。

历史去重文件：

```text
data/history/news-history.json
data/history/learning-history.json
```

这些文件由 GitHub Actions 自动维护并提交，只包含公开标题、来源、链接、分类/类型和去重键，不包含公司密钥，默认保留 30 天。

学习资源 JSON 也会保留：
- `curated_items`：默认 1 条，供“Codex Agent 每日一学”直接使用。
- `history_dedupe`：学习资源历史去重统计。

当前新闻重点已调整为：
1. AI Agent / AI 编程工具 / Codex / MCP / Agent workflow。
2. AI 组织变革、研发提效、企业流程自动化、软件工程提效。
3. AI + 车载 OBC / DCDC / 功率电子 / 电源控制 / 故障诊断 / 预测性维护 / 数字孪生。
4. 头部公司官方研究报告、技术博客、白皮书、研究论文和官方技术文章。
5. 其他重大 AI 技术新闻。

自动驾驶和智能座舱现在只是补充方向，不再是默认主线。只有明确涉及 AI Agent、车载 Agent、研发提效、软件工程工具链、OBC/DCDC/功率电子、AI 芯片或车载算力平台技术进展、重大模型/算法/工具链突破时，才会优先进入候选。

信息源不仅包含新闻媒体，也包含头部公司官方研究报告、技术博客、白皮书和论文。官方技术内容优先级高于媒体转载；同一事件重复时，保留顺序为：官方研究报告 > 官方技术博客 > 白皮书 > 高质量技术媒体 > 普通媒体转载 > Google News 聚合链接。默认 `curated_items` 中会尽量保留 1 条高质量公司研究/技术报告。

## 10.2 RSS 策展配置
可通过环境变量调整，不填写则使用默认值：

```bash
NEWS_REGION_MODE=balanced
NEWS_TOP_N=5
TARGET_CANDIDATE_COUNT=5
MIN_GLOBAL_NEWS=2
MAX_CHINA_NEWS=2
MAX_AUTO_CHINA_NEWS=1
MAX_SAME_SOURCE_NEWS=2
MAX_ITEMS_FOR_LLM=5
MIN_AGENT_NEWS=1
MIN_PRODUCTIVITY_NEWS=1
MIN_COMPANY_RESEARCH_NEWS=1
MAX_COMPANY_RESEARCH_NEWS=2
MAX_AUTO_DRIVING_NEWS=1
MAX_SMART_COCKPIT_NEWS=1
POWER_ELECTRONICS_BOOST=true
CANDIDATE_RETENTION_DAYS=3
HISTORY_DEDUPE_DAYS=14
HISTORY_RETENTION_DAYS=30
HISTORY_SIMILARITY_THRESHOLD=0.82
TARGET_LEARNING_CANDIDATE_COUNT=1
MAX_SAME_SOURCE_LEARNING=1
LEARNING_HISTORY_DEDUPE_DAYS=14
LEARNING_HISTORY_RETENTION_DAYS=30
LEARNING_HISTORY_SIMILARITY_THRESHOLD=0.82
```

调参建议：
- 如果日报中国新闻太多：降低 `MAX_CHINA_NEWS`、降低 `MAX_AUTO_CHINA_NEWS`、提高 `MIN_GLOBAL_NEWS`。
- 如果日报全球新闻太多：提高 `MAX_CHINA_NEWS` 或 `MAX_AUTO_CHINA_NEWS`。
- 如果日报官方报告太多：降低 `MAX_COMPANY_RESEARCH_NEWS`。
- 如果希望更多技术深度：提高 `MIN_COMPANY_RESEARCH_NEWS` 或 `TARGET_CANDIDATE_COUNT`。
- 如果普通自动驾驶/智能座舱仍然太多：降低 `MAX_AUTO_DRIVING_NEWS` 和 `MAX_SMART_COCKPIT_NEWS`。
- 如果当天重复新闻仍然很多：检查 `dedupe_key` / normalized title，并降低相似度阈值，例如 `0.82` 改为 `0.78`。
- 如果跨天重复新闻仍然很多：提高 `HISTORY_DEDUPE_DAYS`，或降低 `HISTORY_SIMILARITY_THRESHOLD`。
- 如果新进展被误杀：提高 `HISTORY_SIMILARITY_THRESHOLD`，降低 `HISTORY_DEDUPE_DAYS`，并检查 `has_new_development_signal`。
- 如果 Codex 每日一学重复：提高 `LEARNING_HISTORY_DEDUPE_DAYS`，或降低 `LEARNING_HISTORY_SIMILARITY_THRESHOLD`。
- 如果学习资源同一来源太多：降低 `MAX_SAME_SOURCE_LEARNING`。
- `CANDIDATE_RETENTION_DAYS` 控制 `data/news-candidates/` 和 `data/learning-candidates/` 只保留最近几天；history 不受影响，仍按各自 retention 保留用于未来去重。

当天重复新闻判断：
- 标题规范化后完全相同，视为重复。
- 标题相似度高于阈值，视为重复。
- 同一标题多来源转载时，优先保留官方研究报告、官方技术博客、白皮书、高质量技术媒体，再看分数、摘要完整度和发布时间。
- Google News 聚合链接不会单独作为强唯一依据，优先按标题判断。

跨天重复新闻判断：
- 最近 `HISTORY_DEDUPE_DAYS` 天内 canonical key 相同，视为旧闻。
- normalized title 相似度超过 `HISTORY_SIMILARITY_THRESHOLD`，视为旧闻。
- normalized link 相同且不是 Google News 聚合链接，视为旧闻。
- 标题/摘要包含明确新进展信号（如发布、上线、量产、开源、benchmark）时可保守放行，并写入 `selection_reason`。
- 如果历史里已有普通媒体转载，但当天出现更高优先级的官方研究、技术博客或白皮书，脚本会优先放行官方来源，并写入 `selection_reason`。

手动测试 GitHub Actions 抓取结果：

```bash
python scripts/collect_rss.py
python -m json.tool data/news-candidates/$(date -d yesterday +%F).json >/tmp/news.json
python -m json.tool data/history/news-history.json >/tmp/history.json
```

Windows Git Bash 下也可以直接运行 `python scripts/collect_rss.py`，然后打开最新的 `data/news-candidates/YYYY-MM-DD.json` 查看 `curated_items`、`rejected_stats` 与 `history_dedupe`。

## 11. 如何手动触发 GitHub Actions
1. 打开 GitHub 仓库 `Actions`。
2. 选择 `Collect RSS Candidates`。
3. 点击 `Run workflow`。

## 12. 如何本地手动运行
Windows：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_daily_ai_news.ps1
```

macOS/Linux：

```bash
./run_daily_ai_news.sh
```

或直接运行：

```bash
python scripts/daily_ai_news.py
```

## 13. Windows 任务计划程序（每天 08:30）
1. 打开任务计划程序。
2. 创建基本任务，名称：`Daily AI News To Feishu`。
3. 触发器：每天 08:30。
4. 操作：启动程序。
5. 程序：`powershell`。
6. 参数：

```text
-NoProfile -ExecutionPolicy Bypass -File "项目完整路径\run_daily_ai_news.ps1"
```

7. 起始于：

```text
E:\Lark_Automation\ai-news-feishu-bot
```

8. 如系统支持，可勾选“唤醒计算机运行此任务”。

注意：电脑睡眠/关机、VPN 断开、公司网关不可达都可能导致任务失败。

## 14. 本地访问不了 GitHub 时
可选方案：
1. 手动下载 `data/news-candidates/*.json` 与 `data/learning-candidates/*.json`。
2. 通过内网文件同步到本地项目目录。
3. 执行 `python scripts/daily_ai_news.py`。

## 15. 依赖说明
- `requirements.txt` 仅保留说明注释。
- 核心脚本全部使用 Python 标准库。
