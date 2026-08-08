# noiz-pseo-voice-cli

[English](README.md) · **简体中文**

用于操作 voice SEO pipeline 的命令行工具：查询管线状态、对单个页面做验收检查、手动创建/更新 voice 页面。人与 AI agent 都可以使用——鉴权只需要一个 CMS 账号，没有其它区分。

## 背景

voice SEO pipeline 通常以内部脚本的形式跑在 runner 机器上。这个 CLI 把同样的操作封装成一条可安装的命令，输出结构化结果，让任何调用方（CI、自动化、agent、运营）都能：

- 查看管线状态和队列健康度
- 对 voice 页面做验收检查（关键词、内容 hash、assets、线上 URL）
- 手动创建候选记录、写入/推进字段——相当于不依赖常驻 runner 的手动管线
- 把音色入队进 pipeline（幂等）
- 留下"谁在什么时候执行了什么"的审计轨迹

## 命令

| 命令 | 类型 | 用途 |
| --- | --- | --- |
| `doctor` | 只读 | 环境/凭据自检 |
| `permissions` | 只读 | 验证账号鉴权并展示权限模型 |
| `status` | 只读 | 管线总览（CMS 状态计数 + 可选队列） |
| `voices list [--status] [--locale] [--limit]` | 只读 | 列出记录 |
| `voices get <id\|voiceId\|slug>` | 只读 | 获取单条记录 |
| `voices create <voiceId> [--name] [--slug] [--status]` | 写 | 创建候选记录 |
| `voices update <id> --set '<json>' [--status]` | 写 | 修改字段 / 推进状态 |
| `check <id\|voiceId\|slug>` | 只读 | 单页验收检查 |
| `queue` | 只读 | 队列计数 + 游标（需要 DB） |
| `dry-run enqueue <voiceId>` / `dry-run consume` | 只读 | 预演写操作，不改状态 |
| `enqueue <voiceId>` | 写 | 音色入队（幂等） |
| `audit [--since] [--caller] [--limit]` | 只读 | 查询本地审计日志 |

所有命令支持 `--json` 结构化输出与 `--help` 自发现；退出码 0=成功、1=失败。

## 安装

```bash
pip install "git+https://github.com/linjizi/noiz-pseo-voice-cli.git@v0.4.1"
# DB 命令（queue / dry-run / enqueue）需要可选依赖：
pip install "noiz-pseo-voice-cli[db] @ git+https://github.com/linjizi/noiz-pseo-voice-cli.git@v0.4.1"
```

## 快速开始

1. 安装（见上）。
2. 创建配置文件（建议 `~/.config/noiz-pseo-voice.env`，然后 `chmod 600`）：

```bash
NOIZ_CMS_URL=https://your-cms.example.com/seo-manage
NOIZ_CMS_API_KEY=sk-xxx            # 或 NOIZ_CMS_EMAIL / NOIZ_CMS_PASSWORD
NOIZ_SITE_BASE=https://example.com
NOIZ_VOICES_DB_URL=postgresql://readonly:xxx@host:5432/db
NOIZ_CALLER_ID=my-agent
```

3. 自检：

```bash
export NOIZ_PSEO_VOICE_CONFIG=~/.config/noiz-pseo-voice.env
noiz-pseo-voice doctor
```

全部 PASS 即可开始使用；FAIL 会告诉你缺哪一项。

## 教程：15 分钟跑通

1. **查看管线总览** —— `noiz-pseo-voice status`
2. **找一条记录** —— `noiz-pseo-voice voices list --status built --limit 10`，再 `noiz-pseo-voice voices get <id>`
3. **验收一个页面** —— `noiz-pseo-voice check <voiceId>`（状态、关键词、内容 hash、assets、线上 URL，逐项 PASS/FAIL）
4. **创建 voice 页面（写）** —— `noiz-pseo-voice voices create <voiceId> --name "My Voice" --status candidate_screening`
5. **写字段 / 推进状态（写）** —— `noiz-pseo-voice voices update <id> --set '{"pipelineStaging":{"keywordInputJson":{"primary_keyword":"tts","validated":true}}}' --status keywords_ready`
6. **入队（写）** —— 先 `noiz-pseo-voice dry-run enqueue <voiceId>`，再 `noiz-pseo-voice enqueue <voiceId>`（重复执行返回 `skipped_dup`）
7. **看审计** —— `noiz-pseo-voice audit --since 2026-08-08T00:00:00Z`

## 示例

给人看（文本）：

```bash
$ noiz-pseo-voice check 7bc8b578
[PASS] status_terminal: built
[PASS] keywords_present: keywordInputJson/keywordInput
[PASS] primary_keyword: classic audiobook narrator
[PASS] keywords_validated: validated flag
[PASS] content_hash: a1b2c3...
[PASS] assets_nonempty: 9 asset(s)
[PASS] page_url_defined: https://example.com/lp/voice/...
[PASS] page_url_live: https://example.com/lp/voice/...
overall: OK (voiceId=7bc8b578)
```

给 agent 看（JSON）：

```bash
$ noiz-pseo-voice check 7bc8b578 --json
{
  "ok": true,
  "checks": [{"name": "status_terminal", "ok": true, "detail": "built"}, ...],
  "voice_id": "7bc8b578",
  "page_url": "https://example.com/lp/voice/..."
}
```

在 CI 里用：

```bash
noiz-pseo-voice check "$VOICE_ID" --json || exit 1
```

## 配置

优先级：环境变量 > `NOIZ_PSEO_VOICE_CONFIG` 指向的 key=value 文件（建议 0600）。

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `NOIZ_CMS_URL` | 是 | Payload CMS base URL（无内部默认值） |
| `NOIZ_CMS_API_KEY` | 二选一 | CMS API key |
| `NOIZ_CMS_EMAIL` / `NOIZ_CMS_PASSWORD` | 二选一 | CMS 账号密码登录 |
| `NOIZ_SITE_BASE` | 否 | 线上页面活链检查基准（默认 `https://noiz.ai`） |
| `NOIZ_VOICES_DB_URL` | DB 命令 | voices 数据库 DSN |
| `NOIZ_CALLER_ID` | 否 | 审计日志里的调用方标识 |
| `NOIZ_AUDIT_LOG` | 否 | 审计日志路径（默认 `~/.local/share/noiz-pseo-voice/audit.jsonl`） |

## 权限模型

- 鉴权只认 CMS 账号（API key 或邮箱密码），没有额外的 agent/人分档。
- 权限与 CMS 对齐：账号能登录，就能执行全部 CLI 命令；命令范围只限 voice-detail-pages pipeline（create/update/enqueue），不碰其它东西。
- 保留两道防误操作但不拦权限的机制：写命令的 `dry-run` 预演 + 每次调用的本地审计日志。
- 凭据只放 0600 配置文件或环境变量，绝不提交进仓库。

## AI agent 使用说明

仓库根目录带一份 `SKILL.md`，AI agent 可以借此自动发现这个工具的命令和安全规则，无需读源码。agent 使用时应：

- 调用前先读 `SKILL.md`（安装、配置、命令清单）。
- 结构化结果优先用 `--json` 输出。
- 任何写命令前先用 `dry-run` 预演，再用 `audit` 核对副作用。
- 凭据只放 0600 配置文件或环境变量，绝不写进 prompt、日志或聊天记录。

## 安全

- 密钥只放环境变量或 0600 配置文件
- 每次调用都有审计日志（caller、命令、结果），可用 `audit` 查询
- 写命令尽量幂等，并可用 `dry-run` 预演

## Agent 技能（skill）

仓库自带 `SKILL.md`：AI agent 安装 CLI 后，可加载该技能自动发现命令、配置和安全规则（适用于 Codex / Claude 类 agent 环境）。见 [SKILL.md](SKILL.md)。

## 开发

```bash
pip install -e ".[dev,db]"
pytest
```

PRD 见 `docs/PRD.md`。许可证：MIT（见 `LICENSE`）。
