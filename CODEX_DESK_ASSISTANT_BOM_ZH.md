# BMO — Orange Pi Zero 3 采购与接线清单

BMO（读作“哔某”）当前硬件方案已经固定为：

> **Orange Pi Zero 3 2GB + ReSpeaker Lite USB + 4.3 英寸 800×480 HDMI 屏**

比赛方的 Orange Pi 3B 和 Tuya T5AI 需要归还；当前 Orange Pi Zero 3 是团队
自购硬件。Codex、GPT 和飞书能力通过网络调用，板子负责唤醒、录音、播放、
屏幕和服务编排。

## 已确定物料

| 材料 | 数量 | 规格 | 状态 |
|---|---:|---|---|
| 主控板 | 1 | Orange Pi Zero 3，2GB | 已购 |
| 音频板 | 1 | ReSpeaker Lite USB 双麦阵列 | 已购 |
| 扬声器 | 1 | 4Ω 5W 腔体，JST-PH 2.0 | 已选 |
| 显示屏 | 1 | 4.3 英寸横屏 IPS，800×480，非触摸 | 已选 |
| 屏幕驱动板 | 1 | HDMI 输入、Mini-HDMI、USB-C 5V | 屏幕套餐内 |

## 仍需确认

| 材料 | 推荐规格 | 说明 |
|---|---|---|
| microSD | 32GB/64GB，Class 10、A1，可靠品牌 | 烧录 Debian 12 |
| 主板电源 | 固定 5V/3A USB-C | 原型主板供电 |
| ReSpeaker 数据线 | USB-A → USB-C，支持数据 | 不能是仅充电线 |
| 视频线 | Micro-HDMI 公 → Mini-HDMI 公短线 | 优先直连、少一个转接头 |
| 屏幕供电线 | USB-C 5V | 套餐已含时不重复购买 |
| 首版外壳 | 纸板/泡沫板/积木结构 | 接口位置确认后再打印 |

套餐若只有“标准 HDMI → Mini-HDMI”线，也可在 Orange Pi 一端增加
“Micro-HDMI 公 → 标准 HDMI 母”转接头，但直连短线更适合最终外壳。

## 最少接线

```text
Orange Pi Zero 3
  ├── Micro-HDMI → Mini-HDMI 屏幕驱动板
  └── USB-A → USB-C ReSpeaker Lite → 4Ω 5W 扬声器
```

原型阶段主板和屏幕分别使用稳定 5V。整机验收后，由硬件开发用固定
5V/4A～5A 电源在外壳内分成主板和屏幕两路，最终外部只保留一根电源线。

## 不需要购买

- ReSpeaker 功放板：Lite 已有扬声器输出。
- USB 声卡或额外麦克风。
- XIAO ESP32S3：本项目走 Orange Pi USB Audio。
- 触摸屏和 USB Hub：当前非触摸屏不占第二个 USB 数据口。
- 摄像头：第一版到岗检测不做人脸识别。
- Raspberry Pi 5、Jetson、AI HAT 或独立显卡。
- 定制 PCB：接口和声学结构尚未真机验收。

## 可选材料

| 材料 | 什么时候再买 |
|---|---|
| 小型被动散热片 | 本地转写连续运行后温度偏高 |
| 毫米波存在传感器 | 软件在席判断误报不能接受 |
| 减震泡棉、声学布 | 正式外壳调试回声与共振 |
| 5V 分电板/定制电源线 | 合并成一根外部电源线 |

## 到货验收

1. Orange Pi 从 microSD 启动官方 Debian 12 Bookworm Server。
2. `aplay -l` 和 `arecord -l` 能看到 ReSpeaker/XMOS。
3. HDMI 屏以 800×480 显示完整 BMO 页面。
4. 扬声器能播放，麦克风能录制中英文。
5. 播放语音时插话能够停止播放并进入下一轮监听。
6. 连续运行两小时无 USB 掉线、欠压重启或明显过热。

完整烧录、接线、安装和验收命令见
[`HARDWARE_BRINGUP_ORANGEPI_ZERO3_ZH.md`](./HARDWARE_BRINGUP_ORANGEPI_ZERO3_ZH.md)。
