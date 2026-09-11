# 交易通知

NiuNo3 参考 NiuOne 的渠道和配置交互，独立实现成交后的异步通知。模块默认关闭，不读取或迁移 NiuOne 的机器人凭据。可在“设置与运行”顶部点击“交易通知设置”直达模块。

## 配置与测试

1. 从下拉框添加飞书、钉钉、企业微信或 Telegram。
2. 填写渠道字段，按需设置 1–30 秒请求超时，默认 5 秒。
3. 点击该卡片的“发送测试通知”。这会向当前渠道实际发送一条测试消息，优先使用未保存输入，留空字段回退到已保存值；不受总开关或渠道开关影响，不保存配置，不生成订单或成交。每个渠道两次测试至少间隔 10 秒。
4. 打开启用模拟成交通知及所需渠道开关，点击“保存通知设置”。保存立即生效，无需重启。

| 渠道 | 必填 | 可选 | 接受的地址 |
| --- | --- | --- | --- |
| 飞书 | 机器人 Webhook | 签名密钥 | `https://open.feishu.cn/open-apis/bot/v2/hook/...` 或 `https://open.larksuite.com/open-apis/bot/v2/hook/...` |
| 钉钉 | 机器人 Webhook | 签名密钥 | `https://oapi.dingtalk.com/robot/send?access_token=...` |
| 企业微信 | 机器人 Webhook | 无 | `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...` |
| Telegram | Bot Token、Chat ID | 无 | 自动使用官方 `api.telegram.org` Bot API |

机器人端启用签名校验时，必须填写原始签名密钥；不填写临时计算的签名或带 timestamp/sign 的 Webhook。如启用了关键词限制，可包含“模拟成交”。Telegram 机器人需要能向对应会话发送消息。创建机器人和安全设置以官方文档为准：[飞书](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot?lang=zh-CN)、[钉钉](https://open.dingtalk.com/document/robots/customize-robot-security-settings)、[企业微信](https://developer.work.weixin.qq.com/document/path/91770)、[Telegram](https://core.telegram.org/bots/api#sendmessage)。

关闭总开关或单个渠道保留凭据；移除并保存后删除当前渠道配置。移除后在保存前重新添加会保留原输入。敏感字段仅显示“已设置／未设置”，留空保存保留旧值；可勾选“保存时清除签名密钥”撤销已保存的可选签名。Bot Token、Webhook、Chat ID 和签名密钥不通过配置 API 回显，不写入通知日志或消息记录。凭据保存在私有 SQLite 数据库中，数据库及备份仍需按私有配置保管。

通知配置使用独立版本，不修改交易配置、不取消交易订单。并发编辑使用版本检查，旧页面需重新加载后再保存。保存时取消旧配置下尚未发送的通知，并从保存时已提交成交的末尾开始记录新通知，避免向新目标补发历史交易。已经开始发送的网络请求无法撤回。

## 成交消息与交付语义

worker 在成交事务成功提交后读取新的成交分录。消息包括 ETF 名称、代码、方向、成交份额和三位小数价格、成交额、费用、卖出已实现盈亏、策略原因、北京时间、订单和成交编号。部分成交按实际新增成交份额通知。同次读取中的多笔成交合并，超过 1800 UTF-8 字节时按完整成交条目拆分，兼容各渠道长度限制。

成交游标与各渠道待发送消息在一个 SQLite 事务中保存。网络发送使用独立的最多 4 个线程，每渠道同一时刻至多一个任务，不占用交易循环。各渠道独立记录结果，任何发送失败都不会回滚成交、资金或持仓。

- 初次安装、总开关关闭期间的成交不补发。
- 尚未开始发送的消息在 worker 重启后继续处理。
- 已确认发送成功的消息不会自动重复发送。
- 渠道明确拒绝时记为“发送失败”；超时、无有效回执或发送过程中断超过 120 秒时记为“送达待确认”。这些情况不会自动重发，避免重复通知。无法保证外部机器人接口的端到端恰好一次送达。
- 请求不跟随重定向，仅接受对应服务的官方 HTTPS 地址。请求有超时及响应大小限制，错误信息不包含上游响应正文或凭据地址。

“通知记录”显示最近 50 条，可展开消息内容，10 秒刷新一次；“运行记录”同时记录通知配置保存和发送结果。通知队列及发送记录随账户数据卷持久保存。

## 管理员 API

所有接口要求管理员会话，写接口还要求同源和 `X-NiuNo3-Request: 1`。

| 接口 | 功能 |
| --- | --- |
| `GET /api/v1/notifications/config` | 配置版本、总开关、超时、渠道及字段是否已设置 |
| `PATCH /api/v1/notifications/config` | 保存配置；需要当前 version、enabled、timeout 及 channels |
| `POST /api/v1/notifications/test/{channel}` | 使用当前输入与已保存字段发送单渠道测试；需要 version、timeout、fields |
| `GET /api/v1/notifications/history?limit=50` | 最近发送记录；最大 100 条 |

渠道配置为 `{added, enabled, fields, clear_signing_secret}`。未包含的渠道保持原值，`added=false` 移除；字段留空保留。测试及保存的输入校验错误只返回受控提示，不回显提交值。
