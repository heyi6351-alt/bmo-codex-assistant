# BMO — Radxa ZERO 3W 采购清单

BMO（读作“哔某”）当前硬件方案已经固定为：

> **Radxa ZERO 3W 2GB + ReSpeaker Lite USB + HDMI 小屏**

不再采购比赛方的 Orange Pi、Tuya T5AI，也不需要 Raspberry Pi 5。Codex、GPT
和飞书能力通过网络调用，板子主要负责唤醒词、录音、播放、屏幕和服务编排。

## 必买材料

| 材料 | 数量 | 推荐规格 | 参考预算 |
|---|---:|---|---:|
| 主控板 | 1 | **Radxa ZERO 3W 2GB，无 eMMC 版** | ¥120–220 |
| 系统盘 | 1 | 32GB 或 64GB 高耐久 microSD，A1/A2 | ¥25–60 |
| 主板电源 | 1 | 稳压 5V/3A USB-C 电源 | ¥25–50 |
| 音频板 | 1 | **ReSpeaker Lite USB 双麦阵列** | ¥180–260 |
| 扬声器 | 1 | 4Ω 5W，带腔体，接 ReSpeaker SPK 插座 | ¥15–40 |
| 显示屏 | 1 | 3.5–5 英寸 HDMI，480×320 或 640×480 | ¥100–220 |
| 视频线 | 1 | Micro HDMI 转屏幕对应 HDMI 接口 | ¥15–30 |
| USB 数据线 | 1 | USB-C 数据线，用于主板 Host 口连接 ReSpeaker | ¥10–25 |
| 屏幕供电 | 1 | 按屏幕要求准备 5V 电源线；原型阶段建议独立供电 | ¥10–30 |
| 外壳 | 1 | 首版纸板/泡沫板，验证后再 3D 打印 | ¥0–100 |

预计总价约 **¥500–900**，以实际购买渠道为准。先购买主板、microSD、音频板
和扬声器；小屏到货前可用普通 HDMI 显示器调试。

## 下单时必须确认

- 主板必须是 **Radxa ZERO 3W 2GB**，不要买成 ZERO 3E 或其他版本。
- microSD 不要使用无品牌卡，持续写日志时容易损坏。
- USB 线必须支持数据，不能是仅充电线。
- ReSpeaker 选择 **Lite USB** 版本，以标准 USB 声卡方式接入 Linux。
- 扬声器阻抗为 **4Ω**、额定功率约 **5W**，购买前与卖家确认插头规格和极性。
- 屏幕必须能接收 HDMI；不要购买只能走树莓派 DSI 的专用屏。
- 主板与屏幕建议先独立供电，整机稳定后再评估合并电源。

## 推荐接线

```text
5V/3A USB-C 电源
        │
        ▼
Radxa ZERO 3W 2GB
  ├── Micro HDMI ─────────────→ HDMI 小屏
  └── USB 3.0 Host Type-C ───→ ReSpeaker Lite USB
                                      │
                                      └── SPK JST → 4Ω 5W 扬声器
```

首版不要走 GPIO 或 I²S，所有音频统一通过 USB。这样系统重装或日后换板时，
只要新板支持 ARM64 Debian、USB 声卡和 HDMI，软件层不需要重新绑定厂商 SDK。

## 可选材料

| 材料 | 什么时候再买 |
|---|---|
| USB Hub | USB 接口实测不够，且 Hub 能稳定供电时 |
| 物理麦克风静音开关 | 需要硬件级隐私控制时 |
| 毫米波存在传感器 | 键盘/鼠标活动、蓝牙在场等软件判断误报明显时 |
| USB 转网口 | 工位 Wi-Fi 不稳定时 |
| 3D 打印外壳 | 屏幕、音频板和扬声器位置验证完成后 |
| 减震泡棉、声学布 | 做正式外壳并调试回声消除时 |

## 当前不要购买

- Orange Pi 或 Tuya T5AI：比赛结束后不再依赖这些板卡。
- Raspberry Pi 5、Jetson、AI HAT 或独立显卡：BMO 不在板上运行大语言模型。
- 普通单麦克风作为最终版本：缺乏可靠回声消除，BMO 播报时容易听见自己。
- 摄像头：第一版的到岗识别优先用电脑解锁、输入设备活动或手机蓝牙。
- 定制 PCB：接口、供电和声学结构尚未在真机完成验证。

## 购买后的验收

硬件同学接线后依次确认：

1. Radxa 能从 microSD 启动 64 位 Radxa OS / Debian Bookworm。
2. `aplay -l` 和 `arecord -l` 能看到 ReSpeaker。
3. HDMI 能显示 BMO 的 640×480 页面。
4. 扬声器能播放测试音，麦克风能录下中英文语音。
5. BMO 播放语音时，用户插话可以终止播放并重新进入监听。
6. 连续运行两小时没有 USB 掉线、欠压重启或明显过热。

完整接线、烧录、安装和验收命令见
[`HARDWARE_BRINGUP_RADXA_ZERO3W_ZH.md`](./HARDWARE_BRINGUP_RADXA_ZERO3W_ZH.md)。
