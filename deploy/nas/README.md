# NAS OpenClaw 配置归档

当前参考版本为 **OpenClaw 2026.10.4**。请从 [`current-2026.10.4/`](current-2026.10.4/README.md) 查看脱敏策略快照与版本说明。

仓库中的当前快照用于记录模型、agent 执行边界、通信和已知插件差异；它不是生产配置，不能直接部署。生产事实以 NAS 当前 Compose、`.env` 和 `data/state/openclaw.json` 为准。公开仓库不保存这些完整文件、凭据、渠道账号、数据库、会话、工作区或浏览器资料。

原 `BASELINE.json`、`PUBLIC-FILES.json`、`compose/`、`operator-files/`、`runtime-overlays/2026.9.6-gh/`、`systemd-user/` 与 `upgrade-kit/` 保留为 **2026.9.6 / 2026-10-07 历史归档**。其中的旧 runtime overlay 和升级入口不属于当前 10.4 部署，不要直接应用到新版本。历史提交保留原始归档及其发布记录。

公开文件只适合审阅定制差异和维护边界，不构成灾备包，也不能取代受保护的 NAS 恢复点。
