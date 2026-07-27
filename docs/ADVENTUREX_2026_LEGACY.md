# AdventureX 2026 原始版本（历史归档）

本页记录仓库在 AdventureX 2026 比赛期间的架构。它不再是当前 BMO Codex
Assistant 的安装说明。

## 当时的硬件

- **Tuya T5AI-Board**：BMO 的表情、语音和触摸屏，代码位于 `app/`。
- **Orange Pi 3B**：运行 Armada daemon 和 trading API。
- **BMO PC Worker**：位于 `bmo_worker/`，负责受权限约束的电脑能力。

这些板卡由主办方提供并需要归还。当前可复刻版本改用 Radxa ZERO 3W 2GB、
ReSpeaker Lite USB 和标准 HDMI 屏。

## 当时的比赛赛道

| Track | Facet file | 当时状态 |
|---|---|---|
| PANDA AI | `app/src/app_trader.c` | 展示 Orange Pi 的交易信号 |
| Photon | `app/src/kaleido.c` → `s_facet_photon` | placeholder |
| StepFun | `app/src/kaleido.c`, `pc_agent/` | in progress |
| LiberNovo | `app/src/app_brain.c` | working |

## 历史代码入口

- `app/`：TuyaOpen / LVGL 9 端界面；
- `board/`：Tuya T5AI 板级配置；
- `bmo_worker/`：PC capability worker；
- `pc_agent/`：比赛阶段的电脑代理；
- `tradingai/`：交易相关代码；
- `BMO_HANDOFF.md`、`KIMI_INSTRUCTIONS.md`：比赛阶段交接说明；
- `flash_fast.ps1`：当时的 Windows 刷机脚本。

旧版需要按住板载 KEY 说话，并依赖 Tuya 与 Orange Pi，因此不要用它部署当前
免按键语音助手。当前安装入口是仓库根目录的 [`README.md`](../README.md)。
