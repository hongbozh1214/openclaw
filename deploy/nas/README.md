# OpenClaw NAS customization snapshot

This branch starts from OpenClaw `main` at commit `0e0563a5f9f1c82fc2790c3d6b1aee9519bb7ec0` (source version 2026.10.5, observed 2026-10-22). The deployment reference under [current-2026.10.4/](current-2026.10.4/README.md) records the NAS runtime as OpenClaw 2026.10.4 on 2026-10-22. The source base and the deployed runtime version are intentionally distinct.

The snapshot is sanitized and read-only reference material. It is not a deployment bundle and must not be copied directly over a live configuration. The NAS deployment source and private runtime state remain authoritative.

The former 2026.9.6 branch, including its older templates, runtime overlays, operator files, and original history, remains preserved at [nas-custom-2026.9.6-20261007](https://github.com/hongbozh1214/openclaw/tree/nas-custom-2026.9.6-20261007). Its own current guide identifies those files as historical; they are not carried forward as active 10.4/10.5 instructions.

The public snapshot excludes credentials, channel account state, sessions, task contents, workspace data, browser profiles, and the complete production configuration.
