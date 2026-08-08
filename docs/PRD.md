# noiz-pseo-voice-cli PRD v1

**状态**：v0.4.0 已发布 · **日期**：2026-08-08

## 1. 背景与目标

现有 voice SEO pipeline 的入口是仓库内脚本（`pipeline_orchestrator.py`、`new_voice_enqueue.py`、`poll_runner.py` 等），只能由内部同学在 runner 机器上调用。外部 agent / 自动化 / CI 无法安全、标准地查询管线状态或做影响评估。

**目标**：把 pipeline 的能力包装成一个外部 agent 可安装、可自发现的 CLI（`noiz-pseo-voice`），统一输出（`--json`）、统一退出码、带审计日志，权限完全跟随调用方 CMS 账号。

**收益**
- 外部 agent / CI / 批量运营可直接调用（query、status、check、dry-run）
- 标准化接口：`--json` + `--help`，AI agent 零适配可用
- 一套 CLI 内部外部复用，避免脚本接口漂移
- private 仓库版本化 + 审计日志，谁调了什么可查

## 2. 非目标（v1 不做）

- 不实现 HTTP 服务/网关（未来要转 API 时 CLI 即内核）
- 不实现 pipeline 编排本身（screening/关键词/内容生成/rebuild 仍在内部 poll_runner/orchestrator 执行）
- 不做 CLI-Anything / SKILL.md 生成（v2 可选增强）
- 不实现任意 CMS 字段编辑（v1 只有一个受控写命令 enqueue）

## 3. 用户与使用场景

- **外部 SEO/GEO agent**：验收某个音色页（`check`）、看管线总览（`status`）、队列健康（`queue`）
- **CI / 自动化**：发布后验证（`check`）、影响预演（`dry-run`）
- **内部同学**：跨机器快速查询（`voices list/get`、`audit`）

## 4. 权限模型（关键设计）

**CLI 权限与 CMS 现有权限对齐（CMS 不改），不区分 agent 或人。**

- **鉴权只认账号密码 / API key**；登录成功即有效用户（v0.4.0 起移除 read/write 分档）
- CMS 无角色区分（能登录的账号对 voice-detail-pages 都可写）→ CLI 同样不分档，登录即可执行全部命令
- 命令范围只限 voice-detail-pages 链路：`voices create` / `voices update` / `enqueue`（pipeline 队列），不碰其它集合
- 每个用户独立配置文件/账号（可单独吊销、审计对得上人）
- 不设权限分档，保留防误操作两道：本地审计日志（含写）+ `dry-run` 预演

## 5. 命令集 v1

全局：所有命令支持 `--json`、`--help`；二进制 `noiz-pseo-voice`；退出码 0=成功、1=失败。

| 命令 | 类型 | 说明 |
| --- | --- | --- |
| `doctor` | 只读 | 环境/凭据自检：CMS 连通、账号鉴权、DB 连通、审计可写 |
| `permissions` | 只读 | 验证账号鉴权（v0.4.0 起无 read/write 分档：任意有效账号=全部命令） |
| `status` | 只读 | 管线总览：CMS 各 pipelineStatus 计数 + 队列计数/游标 |
| `voices list [--status] [--locale] [--limit]` | 只读 | 记录列表：id/voiceId/slug/status/updatedAt |
| `voices get <id\|voiceId\|slug>` | 只读 | 单条详情（depth=2） |
| `voices create <voiceId> [--name] [--slug] [--status]` | 写 | 手动建候选记录（=消费端 create_candidate 的手动版） |
| `voices update <id> --set '<json>' [--status]` | 写 | 手动 PATCH 字段/推进状态（=手动版 pipeline 的写操作） |
| `check <id\|voiceId\|slug>` | 只读 | 验收检查：状态/关键词/内容 hash/assets/页面 URL 200，逐项 PASS/FAIL |
| `queue` | 只读 | 队列计数 + 游标（需 DB 只读连接） |
| `dry-run enqueue <voiceId>` / `dry-run consume` | 只读 | 写命令影响预演，不动库 |
| `enqueue <voiceId>` | 写（任意有效账号） | 把音色入队 voices_pipeline_queue（幂等 ON CONFLICT DO NOTHING） |
| `audit [--since] [--caller] [--limit]` | 只读 | 本地审计日志查询 |

`voices create/update` 使 CLI 成为**手动版 pipeline 的控制面**：操作者可以手工建候选、写关键词/内容/资产字段、推进状态，绕过常驻 poll_runner；LLM 生成类步骤（关键词/正文）仍需内部 orchestrator 或操作者提供内容后写入。

## 6. 技术方案

- Python ≥3.10，仅标准库；DB 命令可选依赖 `psycopg2-binary`（`pip install noiz-pseo-voice-cli[db]`）
- 包结构：`src/noiz_pseo_voice/`（config/cms/db/audit/checks/commands/cli）
- 配置优先级：环境变量 > `NOIZ_PSEO_VOICE_CONFIG` 指向的 0600 key=value 文件
- 密钥不进仓库；README 提供模板
- CMS 走 Payload REST API（`NOIZ_CMS_URL`，test/prod 可切）；队列/游标走只读 DB（`NOIZ_VOICES_DB_URL`）
- 审计：本地 JSONL（`~/.local/share/noiz-pseo-voice/audit.jsonl`，可用 `NOIZ_AUDIT_LOG` 覆盖）
- 安装：`pip install git+https://github.com/linjizi/noiz-pseo-voice-cli.git`

## 7. 安全

- 凭据只经环境变量/0600 配置文件，不进仓库、不进日志
- 写命令门槛：有效 CMS 凭据 + DB 写连接（如适用）；`dry-run` 先行
- 审计日志记录 caller/命令/参数/结果
- 仓库 private；外部 agent 凭据建议只读角色，最小权限

## 8. 验收标准（v1）

1. `pip install` 后 `noiz-pseo-voice --help` / `--version` 正常
2. 无凭据时 `doctor`/`permissions` 明确报缺项，不崩溃
3. 有效账号：全部命令可用（范围仅 voice-detail-pages 链路）；无效凭据：命令报 CMS 401/403
4. 写账号：`voices create` 可建候选、`voices update` 可 PATCH 字段/状态；`enqueue` 幂等（重复执行 skipped_dup）；`dry-run enqueue` 先报 would-enqueued/skipped_dup
5. `check` 对 built 记录全 PASS、对 pending/缺字段记录逐项 FAIL 且输出明确
6. 审计日志：每次调用可查（caller/command/args/ok）
7. 真实 CMS 实测：voices list/get/check 数据跑通
8. 全命令 `--json` 输出合法 JSON、退出码符合契约

## 9. 里程碑

- M1：v1 实现 + 本地自测（本 PRD 同日）
- M2：owner 验收（真实 CMS 数据）
- M3：发布 tag + README 完善；v2 评估（SKILL.md 生成、更多写命令、网关）
