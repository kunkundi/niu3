# 项目交付约定

- 用户要求：每次应用更新完成后，都要部署到运行站点 `https://niualpha.com`，并验证生效后再结束任务；除非用户明确要求本次不部署。
- 用户已授权此项目的常规更新部署，无需每次再次询问。先完成与修改相关的测试和构建，再按 `docs/operations.md` 的 Docker Compose 流程部署。
- 仅前端修改时，重建镜像并更新 `dashboard`，保持 `worker` 运行；后端或共享运行逻辑变化时更新受影响服务。
- 部署后检查容器健康、本机与公网 `/healthz`，并核实公开站点使用新资源、相关交互已更新。遇到阻塞应明确说明，不把“只改了代码”当作已完成部署。
- 保留现有账户、配置、名单及交易数据；禁止对运行项目执行 `docker compose down -v` 或覆盖正式数据库。

## Commit 风格

参考 NiuOne 的 `AGENTS.md` 及近期提交记录，后续提交遵循以下约定：

- 使用 Conventional Commits 格式：`type(scope): subject`，必须包含 `scope`。
- 常用 `type`：`feat`、`fix`、`refactor`、`perf`、`docs`、`test`、`chore`，按变更性质选择。
- `scope` 对应主要修改领域，例如 `dashboard`、`market`、`settings`、`strategy`、`trading`、`notifications`、`container`、`readme`、`app`。修改交易行为使用 `fix(trading): ...` 等明确领域的标题，不写成无 scope 的 `fix: ...`。
- 提交信息只写一行标题，不写正文。`subject` 使用清晰具体的英文祈使表达，小写开头，不加句号。
- 标题直接说明改了什么及主要影响对象，避免只写 `update`、`improve`、`unify` 等泛泛描述；一个提交只承载一个逻辑变更。
- PR 标题沿用同一格式，并应能直接作为 squash commit 标题。

示例：

```text
feat(dashboard): add account holdings to signal tabs
fix(dashboard): expand ETF charts within mobile watchlist rows
docs(app): document commit message conventions
```
