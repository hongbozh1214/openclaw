# OpenClaw 2026.10.4: sanitized deployment reference

This directory records the accepted 2026-10-18 production cutover and a read-only configuration projection recorded on 2026-10-22. It is not a production configuration copy and is not directly deployable. `FILES.json` records byte lengths and SHA-256 checksums for the listed files, excluding itself.

The source branch is based on OpenClaw `main` at commit `0e0563a5f9f1c82fc2790c3d6b1aee9519bb7ec0` (2026.10.5); the NAS runtime snapshot remains explicitly versioned as 2026.10.4. Updating the source branch does not change or imply an update to the NAS deployment.

The redacted configuration records model inheritance, five agents' execution boundaries, communication permissions, browser modes, and the accepted plugin differences. It omits channel accounts, credentials, sessions, task contents, browser data, and the full production configuration.

## Runtime snapshot

- OpenClaw 2026.10.4; image ID and official base digest are recorded in the JSON.
- Fixed official base with the existing single tools layer; the accepted cutover added no core patches or long-term image kinds.
- Tencent Weixin plugin: `@tencent-weixin/openclaw-weixin@2.4.10`.
- Excel retains four tool-contract declarations and the `xlsx` default-import compatibility change. Check the target upstream version for equivalent support before carrying this difference forward.
- main, both job-search agents, and wei_assistant use their individual sandboxes; maintenance uses Gateway execution with human approval.
- Owner identity, entry-point authentication, and channel identity details are intentionally omitted.

The 2026.9.6 files and history remain in the [historical branch](https://github.com/hongbozh1214/openclaw/tree/nas-custom-2026.9.6-20261007); they are not active instructions here. Use the target version's supported schema and the live NAS configuration for any future change.
