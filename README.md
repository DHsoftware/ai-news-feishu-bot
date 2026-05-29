# AI News Feishu Bot (Hybrid Architecture)

## 1. 项目说明
本项目已改为两阶段混合架构：

A. GitHub Actions（公网侧）  
- 只负责抓取公网 RSS 新闻。  
- 生成候选新闻 JSON：`data/news-candidates/YYYY-MM-DD.json`。  
- 不调用公司 LiteLLM。  
- 不调用飞书。  
- 不保存公司密钥。  

B. 本地电脑（公司内网侧）  
- 先 `git pull` 拉取最新 JSON。  
- 读取 JSON，调用公司 LiteLLM 总结。  
- 发送飞书群机器人 Webhook（每天只发送 1 条日报消息）。  
- 本地脚本仅使用 Python 标准库。  

## 2. 安全边界
- GitHub Actions 不使用、不保存以下信息：
  - `LITELLM_API_KEY`
  - `OPENAI_API_KEY`
  - `FEISHU_WEBHOOK_URL`
  - `FEISHU_BOT_SECRET`
- 以上信息仅存在本地 `.env`。

## 3. 项目结构

```text
ai-news-feishu-bot/
  .github/
    workflows/
      collect-rss.yml
  data/
    news-candidates/
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
- 如果 LiteLLM 仅在 VPN 可访问，定时任务运行时 VPN 必须在线。
- 如果电脑睡眠或关机，任务不会执行。

## 5. 环境变量（本地）
复制模板：

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
- `TIMEZONE`、`NEWS_MAX_CHARS`、`NEWS_TOP_N` 不填会使用默认值。
- `FEISHU_BOT_SECRET` 是飞书机器人“签名校验”里的密钥。
- `FEISHU_BOT_SECRET` 不是 Webhook URL 最后的 token。
- 如果飞书机器人未开启签名校验，`FEISHU_BOT_SECRET` 应留空。
- 请勿提交 `.env`。

## 6. GitHub Actions：RSS 采集阶段

工作流文件：  
- `.github/workflows/collect-rss.yml`

触发方式：  
- 定时：每天 `07:50 Asia/Shanghai`（cron 用 UTC 配置）。  
- 手动：`workflow_dispatch`。

执行内容：  
1. 运行 `python scripts/collect_rss.py`。  
2. 生成/更新 `data/news-candidates/YYYY-MM-DD.json`。  
3. 自动 commit 并 push 回仓库（`contents: write`）。

## 7. 本地日报阶段

入口脚本：  
- `run_daily_ai_news.ps1`

执行逻辑：  
1. 进入项目目录。  
2. 先执行 `git pull`。  
3. `git pull` 失败时不中断，继续尝试使用本地缓存 JSON。  
4. 运行 `python scripts/daily_ai_news.py`。  

`daily_ai_news.py` 逻辑：  
1. 优先读取 `data/news-candidates/YYYY-MM-DD.json`（日报日期为“昨天”）。  
2. 若目标文件不存在，读取最近一个 JSON，并在飞书消息中说明“使用最近缓存”。  
3. 调用 LiteLLM：`POST {LITELLM_BASE_URL}/chat/completions`。  
4. 要求 LiteLLM 仅输出严格 JSON（不输出 Markdown/HTML），schema 仅保留：
   - `title`
   - `summary`
   - `top_news[]`（含 `what_happened`、`why_important`、`auto_relevance`、`auto_impact_brief`、`source_name`、`source_url`）
5. 仅发送 1 条飞书消息：
   - 优先 `interactive` 卡片
   - 失败降级为 1 条 `post`
   - 再失败降级为 1 条 `text`
6. 不再生成 `2/3`、`3/3` 分片消息，不再刷屏。  
7. 详细汽车行业影响已压缩到每条新闻的 `auto_impact_brief`（1-2 句短结论）。  
8. 长 URL 不直接展示，卡片中用“查看来源”按钮。  
9. 若开启 `FEISHU_BOT_SECRET`，自动加签。  
10. 内容过长时不拆消息，优先裁剪单条新闻字段长度并收缩 Top N 数量，保证可读性。  

飞书签名补充：
- 当 `FEISHU_BOT_SECRET` 为空时，脚本不会发送 `timestamp` 和 `sign`。
- 当 `FEISHU_BOT_SECRET` 不为空时，脚本会按飞书签名规则发送顶层 `timestamp` 与 `sign`。
- 若返回 `code=19021`，除检查 secret 配置外，还需检查电脑系统时间是否准确（时间偏差超过 1 小时会失败）。

## 8. 如何手动触发 GitHub Actions
1. 打开 GitHub 仓库页面。  
2. 进入 `Actions`。  
3. 选择 `Collect RSS Candidates`。  
4. 点击 `Run workflow`。  
5. 运行成功后，仓库中会更新 `data/news-candidates/*.json`。

## 9. 如何本地手动运行

Windows：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_daily_ai_news.ps1
```

macOS/Linux：

```bash
./run_daily_ai_news.sh
```

或直接：

```bash
python scripts/daily_ai_news.py
```

## 10. Windows 任务计划程序（每天 08:30）
1. 打开“任务计划程序”。  
2. 创建基本任务，名称：`Daily AI News To Feishu`。  
3. 触发器：每天 `08:30`。  
4. 操作：启动程序。  
5. 程序/脚本：`powershell`。  
6. 参数：

```text
-NoProfile -ExecutionPolicy Bypass -File "项目完整路径\run_daily_ai_news.ps1"
```

7. 起始于：

```text
E:\Lark_Automation\ai-news-feishu-bot
```

8. 可选勾选“唤醒计算机运行此任务”。

注意：
- 电脑睡眠/关机会导致任务不执行。  
- VPN 断开可能导致 LiteLLM 调用失败。  

## 11. 如果本地访问不了 GitHub
可选方案：
1. 手动从仓库下载 `data/news-candidates/YYYY-MM-DD.json` 到本地项目目录。  
2. 或将该 JSON 通过内网文件同步方式同步到本地。  
3. 然后直接运行 `python scripts/daily_ai_news.py`。

## 12. 依赖说明
- `requirements.txt` 仅保留说明性注释。  
- 核心脚本（`collect_rss.py`、`daily_ai_news.py`）均使用 Python 标准库。  
