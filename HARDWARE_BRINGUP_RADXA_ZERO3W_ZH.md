# BMO 硬件交接：Radxa ZERO 3W 2GB

本文档交给硬件开发直接执行。目标是在不使用 Orange Pi、Tuya T5AI、
Windows 或手机的情况下，让 Radxa ZERO 3W 完成 BMO 的显示、收音、播放和
Linux 服务启动。

## 任务卡

- **目标**：Radxa ZERO 3W 2GB 启动 Debian，显示 BMO 表情，并识别
  ReSpeaker Lite 的录音和播放设备。
- **影响对象**：Radxa ZERO 3W、microSD、HDMI 屏、ReSpeaker Lite、
  4Ω 5W 扬声器、电源和线材。
- **验收标准**：本文末尾“硬件验收”全部通过。
- **验证方式**：运行 `check-radxa-hardware.sh`，再完成人工录放音测试。
- **风险等级**：中。主要风险是接错电源口、使用非 5V 电源、扬声器极性
  错误和 USB 线只有充电功能。
- **依赖**：可联网的 64 位 Radxa OS / Debian、公开 GitHub 仓库、
  ReSpeaker Lite 的 USB 固件。

## 分工

### 硬件开发负责

1. 核对物料和接口。
2. 断电接线。
3. 烧录 Radxa OS / Debian。
4. 克隆仓库并运行安装脚本。
5. 完成 HDMI、USB、ALSA、扬声器和 systemd 验收。
6. 把硬件自检输出交给软件开发。

### BMO 所有者负责

1. `codex login --device-auth` 的网页授权。
2. 飞书应用初始化、权限开通和用户授权。
3. API 费用、日历写入权限和 Codex 项目目录授权。

硬件开发不需要也不应接触 OpenAI API Key、Codex token、飞书
appSecret 或用户 access token。

## 确认物料

| 物料 | 要求 | 数量 |
|---|---|---:|
| 主板 | Radxa ZERO 3W，2GB，无 eMMC | 1 |
| 系统盘 | 32GB 或更大、高耐久 microSD | 1 |
| 主板电源 | 稳压 5V/3A USB-C | 1 |
| 屏幕 | 3.5 英寸 HDMI，480×320 或 640×480 | 1 |
| 屏幕电源 | 按屏幕说明提供独立 5V 电源 | 1 |
| 视频线 | Micro HDMI → 屏幕 HDMI | 1 |
| 音频板 | ReSpeaker Lite USB 2-Mic Array | 1 |
| 音频数据线 | 支持数据的 USB-C 线 | 1 |
| 扬声器 | 4Ω 5W 封闭式，匹配 JST 插头 | 1 |
| USB Hub | 只有触摸屏还需要 USB 时再使用 | 可选 |

## 接线图

```text
                  ┌──────────────────────┐
5V/3A USB-C ─────▶│ USB 2.0 OTG / POWER │
                  │                      │
3.5" HDMI 屏 ◀────│ Micro HDMI           │  Radxa ZERO 3W
                  │                      │
ReSpeaker Lite ◀──│ USB 3.0 HOST Type-C │
                  └──────────────────────┘
                           │
                           │ ReSpeaker SPK JST
                           ▼
                    4Ω 5W 扬声器
```

### 逐项说明

1. **主板电源**
   - 接 Radxa 的 USB 2.0 OTG / Power Type-C 口。
   - 只允许稳定的 5V 输入。不要把可调电源直接设成 9V、12V 或 20V。
   - 外设接入后建议 5V/3A，不使用普通低功率充电线。

2. **显示屏**
   - Radxa Micro HDMI 接屏幕 HDMI。
   - 屏幕使用自己的 5V 电源，不从 GPIO 取电。
   - 第一轮调试可以用普通 HDMI 显示器代替小屏。

3. **ReSpeaker Lite**
   - 接 Radxa 的 USB 3.0 HOST Type-C 口。
   - 使用支持数据的 USB 线。
   - 本项目只使用 USB Audio，不连接 40-pin GPIO，也不走 I2S。
   - 如果增加 USB 触摸屏，使用小型有源 Hub，避免接口和供电不足。

4. **扬声器**
   - 接 ReSpeaker Lite 的 SPK JST，不接 Radxa GPIO。
   - 使用 4Ω 5W 封闭式扬声器。
   - 接线前断电，核对插头方向和正负极。

## 上电前检查

- [ ] Radxa 型号与内存为 ZERO 3W 2GB。
- [ ] 主板供电为 5V，不是 9V/12V。
- [ ] USB-C 音频线支持数据。
- [ ] Micro HDMI 没有被误插到 USB-C 位置。
- [ ] ReSpeaker 与扬声器 JST 插头方向正确。
- [ ] 裸板底部没有接触金属桌面或螺丝。
- [ ] 麦克风开孔没有被外壳遮挡。
- [ ] 扬声器和麦克风之间有泡棉或结构隔离，减少机壳共振。

## 烧录系统

使用 Radxa 官方为 ZERO 3W 提供的 64 位 Debian/Radxa OS 镜像，优先选择
Bookworm CLI/Minimal。不要使用 Android 镜像。

官方文档：
<https://docs.radxa.com/en/zero/zero3/getting-started>

烧录完成后：

```bash
uname -m
cat /proc/device-tree/model
```

预期：

```text
aarch64
Radxa ZERO 3W
```

先设置网络、时区和 SSH，再更新系统：

```bash
sudo apt update
sudo apt full-upgrade -y
sudo timedatectl set-timezone Asia/Shanghai
```

## 安装 BMO

仓库公开后使用：

```bash
git clone https://github.com/heyi6351-alt/bmo-codex-assistant.git
cd bmo-codex-assistant/voice_assistant
sudo ./deploy/install-radxa-zero3w.sh
sudo ./deploy/install-voice-models.sh
```

安装脚本会：

- 验证 ARM64 与 Radxa ZERO 3W 型号；
- 创建专用 `bmo` Linux 用户；
- 安装音频、Python、Chromium 和 Xorg 依赖；
- 把运行文件安装到 `/opt/bmo/voice_assistant`；
- 创建 Python 虚拟环境；
- 安装三个 systemd 服务，但不会替用户完成账号授权。

## 软件账号授权

以下步骤由 BMO 所有者执行。

### Codex

官方 standalone 安装器不依赖 npm：

```bash
sudo -iu bmo
curl -fsSL https://chatgpt.com/codex/install.sh | CODEX_NON_INTERACTIVE=1 sh
codex login --device-auth
codex login status
```

在另一台有浏览器的设备打开 CLI 给出的地址并输入设备码。不要把
`~/.codex/auth.json` 上传、发群或复制进仓库。

### 飞书 CLI

仍在 `bmo` 用户下执行：

```bash
npm config set prefix "$HOME/.npm-global"
npm install -g @larksuite/cli
npx -y skills add larksuite/cli -g
lark-cli config init --new
```

初始化完成后，按最小权限为用户身份授权日历、任务和消息域：

```bash
lark-cli auth login --domain calendar,task,im
```

飞书应用后台 scope 与用户授权缺一不可。BMO 必须使用 `--as user` 才能看到
所有者自己的日历。

## 音频识别与配置

先确认 USB：

```bash
lsusb
arecord -l
aplay -l
```

列表中应出现 ReSpeaker、XMOS 或 XU316。然后列出 Python 音频设备：

```bash
sudo -iu bmo
cd /opt/bmo/voice_assistant
.venv/bin/python -m bmo_voice --list-audio-devices
```

编辑 `/etc/bmo/voice.env`，让 `BMO_AUDIO_DEVICE` 等于设备名中唯一、稳定的
一段文本，默认值为：

```text
BMO_AUDIO_DEVICE=ReSpeaker
```

录音测试：

```bash
arecord -D default -f S16_LE -r 16000 -c 1 -d 5 /tmp/bmo-mic.wav
aplay /tmp/bmo-mic.wav
```

播放音量从低到高调节，避免第一次测试满音量：

```bash
alsamixer
```

## 启动服务

先检查配置：

```bash
sudo -iu bmo
cd /opt/bmo/voice_assistant
set -a
. /etc/bmo/voice.env
set +a
.venv/bin/python -m bmo_voice --check
```

返回配置 OK 后：

```bash
sudo systemctl enable --now bmo-display.service
sudo systemctl enable --now bmo-kiosk.service
sudo systemctl enable --now bmo-voice.service
```

查看日志：

```bash
journalctl -u bmo-display -u bmo-kiosk -u bmo-voice -f
```

## 硬件验收

在仓库的 `voice_assistant` 目录执行：

```bash
sudo ./deploy/check-radxa-hardware.sh
```

必须全部通过：

- [ ] 识别 Radxa ZERO 3W。
- [ ] 系统架构为 aarch64。
- [ ] `lsusb` 识别 ReSpeaker/XMOS。
- [ ] ALSA 同时存在录音和播放设备。
- [ ] HDMI 状态为 connected。
- [ ] BMO 表情服务健康检查通过。
- [ ] 屏幕显示 BMO 的眼睛和嘴。
- [ ] 说“BMO / 哔某”后状态切到 LISTENING。
- [ ] 中文和英文各完成一次问答。
- [ ] Codex 问题、coding 指令和飞书日程查询各验证一次。
- [ ] 重启后 display、kiosk、voice 三项服务自动恢复。

## 故障定位

### ReSpeaker 完全没有出现

1. 换确认支持数据的 USB 线。
2. 确认接的是 USB 3.0 HOST，不是主板供电口。
3. 运行 `dmesg -w` 后重新插拔 ReSpeaker。
4. 用 Seeed 官方工具确认 ReSpeaker Lite 已刷 USB firmware 2.0.5 或更新版本。

### 有录音设备但没有播放

1. 检查 `aplay -l`。
2. 检查 ReSpeaker 的 USB firmware 是否为 USB Audio 版本。
3. 在 `alsamixer` 取消静音并降低初始音量。
4. 核对扬声器阻抗、JST 插头和极性。

### HDMI 黑屏

1. 先用普通 HDMI 显示器排除小屏问题。
2. 检查 `/sys/class/drm/*/status`。
3. 按屏幕说明在内核启动参数中设置 480×320 或 640×480。
4. 确认小屏使用独立 5V 电源。

### 2GB 内存不足

1. 保持 Debian Minimal。
2. 只运行一个 Chromium kiosk 页面。
3. 使用 `ggml-base.bin`，不要在本板运行 large Whisper。
4. 大型 coding 仓库放到工位电脑，本板只转发任务。

## 给硬件开发的简短交接消息

> 主板固定为 Radxa ZERO 3W 2GB。主板用 USB 2.0 OTG/Power 口接 5V/3A，
> Micro HDMI 接屏，USB 3.0 Host 接 ReSpeaker Lite，4Ω 5W 扬声器只接
> ReSpeaker 的 SPK JST。不要接 GPIO/I2S。先用普通 HDMI 显示器跑通
> `install-radxa-zero3w.sh` 和 `check-radxa-hardware.sh`，把完整自检输出
> 发给软件同学，再装 3.5 英寸屏和打印外壳。
