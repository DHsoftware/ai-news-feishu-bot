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

说明：
- 代码优先读取 `LITELLM_API_KEY`，兼容 `OPENAI_API_KEY`。
- `TIMEZONE`、`NEWS_MAX_CHARS`、`NEWS_TOP_N` 不填也可运行。
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
- `published_at`
- `source`
- `source_type`（`youtube_video` / `official_doc` / `blog` / `tutorial`）
- `language`
- `region`
- `link`
- `tags`

限制说明：
- YouTube RSS 只能拿到标题、链接、发布时间、简介等元数据。
- 不等于拿到完整字幕或完整视频内容。
- 因此“Codex Agent 每日一学”只会基于元数据提炼学习建议，并附原链接。

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
1. 读取 `data/news-candidates/YYYY-MM-DD.json`，缺失时回退最近缓存。
2. 读取 `data/learning-candidates/YYYY-MM-DD.json`，缺失时回退最近缓存。
3. 从 learning candidates 选择 1 条“Codex Agent 每日一学”资源（优先官方 Codex > YouTube > 教程）。
4. 调用 LiteLLM：`POST {LITELLM_BASE_URL}/chat/completions`。
5. 解析严格 JSON（`summary`、`top_news`、`codex_learning`）。
6. 飞书发送仅 1 条消息（interactive 失败降级 post，再降级 text）。

飞书消息结构：
1. 今日摘要
2. 重要新闻 Top N
3. Codex Agent 每日一学

如果学习资源为空：
- 第三部分显示“今日未发现新的 Codex Agent 学习资源。”

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
