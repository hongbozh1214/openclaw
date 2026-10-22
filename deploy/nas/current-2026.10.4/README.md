# OpenClaw 2026.10.4：脱敏现状参考

此目录是从已接受的 2026-10-18 正式切换记录及 2026-10-22 只读配置投影整理的公开参考。它不是生产配置副本，不能直接部署。`FILES.json` 提供本目录公开文件的字节数与 SHA-256（清单不包含自身）。生产仍以 NAS 上的正式 Compose、`.env` 和 `data/state/openclaw.json` 为准。

`openclaw-config.redacted.json` 记录了模型继承、五个 agent 的执行边界、通信许可、浏览器模式及本次插件差异。路径已归一化，且未包含渠道账号、凭据、会话、任务正文、浏览器资料或完整生产配置。

## 当前版本摘要

- OpenClaw 2026.10.4，固定镜像 ID 和官方基底 digest 见 JSON。
- 官方固定基底沿用现有单一工具层；本次切换没有增加核心补丁或长期镜像种类。
- 微信使用腾讯官方 `@tencent-weixin/openclaw-weixin@2.4.10`。
- Excel 保留四项原生工具契约声明和 `xlsx` 默认导入兼容差异；未来版本需先核实上游是否已等效支持。
- main、两个求职 agent 与 wei_assistant 使用各自沙盒；maintenance 使用 Gateway 执行环境并保留人工审批。
- 单一 Owner、入口认证及渠道身份细节不在公开快照内。

`../BASELINE.json`、`../PUBLIC-FILES.json`、`../compose/` 和 `../runtime-overlays/2026.9.6-gh/` 是 2026.9.6/2026-10-07 历史归档，不代表当前 10.4 运行配置；不要用于重建或回放旧补丁。

该目录只作版本记录和维护参考。不要将其中片段直接合并进生产 `openclaw.json`；变更时以目标版本的原生 schema 和现行 NAS 配置为准。
