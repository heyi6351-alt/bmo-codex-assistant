# BMO 硬件交接：Orange Pi Zero 3 2GB

本文档交给硬件开发直接执行。目标是让团队自购的 Orange Pi Zero 3 完成
BMO 的显示、收音、播放和 Linux 服务启动，不再依赖比赛方的 Orange Pi 3B、
Tuya T5AI、Windows 或手机。

## 任务卡

- **目标**：Orange Pi Zero 3 2GB 启动 Debian 12，显示 BMO 表情，并识别
  ReSpeaker Lite 的录音和播放设备。
- **影响对象**：Orange Pi Zero 3、microSD、4.3 英寸 HDMI 屏、ReSpeaker
  Lite、4Ω 5W 扬声器、电源和线材。
- **验收标准**：本文末尾“硬件验收”全部通过。
- **验证方式**：运行 `check-orangepi-zero3-hardware.sh`，再完成人工录放音
  和中英文问答测试。
- **风险等级**：中。主要风险是烧错板型镜像、使用非 5V 电源、USB 线只有
  充电功能，以及 Micro/Mini HDMI 接口混淆。
- **依赖**：可联网的官方 64 位 Debian 12 Bookworm Server 镜像、公开 GitHub
  仓库、ReSpeaker Lite 的 USB Audio 固件。

## 当前硬件

| 物料 | 已确定规格 | 数量 |
|---|---|---:|
| 主板 | Orange Pi Zero 3，2GB LPDDR4 | 1 |
| 系统盘 | 32GB 或 64GB、Class 10/A1 microSD | 1 |
| 主板电源 | 稳定 5V/3A、USB-C | 1 |
| 屏幕 | 4.3 英寸横屏 IPS、800×480、非触摸 | 1 |
| 屏幕驱动板 | HDMI 输入、Mini-HDMI 插座、USB-C 5V 供电 | 1 |
| 视频线 | 优先 Micro-HDMI 公 → Mini-HDMI 公短线 | 1 |
| 音频板 | ReSpeaker Lite USB 2-Mic Array | 1 |
| 音频数据线 | USB-A 公 → USB-C 公，必须支持数据 | 1 |
| 扬声器 | 单声道 4Ω 5W 腔体喇叭、JST-PH 2.0 | 1 |
| 最终整机电源 | 稳定 5V/4A～5A + 内部分电，仅在整机合并供电时使用 | 可选 |

## 接线

```text
主板 5V/3A USB-C ───────────────▶ Orange Pi Zero 3 POWER

Orange Pi Micro-HDMI ───────────▶ Mini-HDMI 屏幕驱动板
屏幕 5V USB-C ──────────────────▶ 屏幕驱动板

Orange Pi USB-A Host ───────────▶ ReSpeaker Lite USB-C
ReSpeaker Lite SPK JST-PH 2.0 ─▶ 4Ω 5W 单声道扬声器
```

### 视频线

屏幕驱动板是 Mini-HDMI 输入，Orange Pi Zero 3 是 Micro-HDMI 输出。优先
使用一根直连短线：

```text
Micro-HDMI 公 → Mini-HDMI 公
```

如果只能使用屏幕套餐附带的“标准 HDMI → Mini-HDMI”线，则在 Orange Pi
一端增加“Micro-HDMI 公 → 标准 HDMI 母”转接头。直连线更短、更稳，适合
最终外壳。

### 供电

原型阶段让主板和屏幕独立使用稳定 5V 供电，先排除欠压问题。功能稳定后，
硬件开发可以使用一个稳定的 5V/4A～5A 电源和内部分电，让成品外部只保留
一根电源线。更大的额定电流代表电源可提供的上限，不会主动灌入设备；电压
必须固定为 5V。

不要使用 9V/12V 快充诱骗线，不要依靠 HDMI 给屏幕供电，也不要在没有核对
电源路径时同时从多个端口反向供电。

### ReSpeaker 与扬声器

- ReSpeaker 使用板载 USB-A Host，不走 GPIO/I²S。
- USB-A → USB-C 线必须支持数据。
- 扬声器只接 ReSpeaker 的白色 SPK 接口，不接 Orange Pi GPIO。
- 不要同时插 3.5mm 音频线；ReSpeaker 会把输出切到 3.5mm 并静音 JST
  扬声器。
- 断电后再插拔 JST，插头有方向，不要强压。

## 上电前检查

- [ ] 主板丝印和订单均为 Orange Pi Zero 3 2GB。
- [ ] microSD 镜像文件名包含 `orangepizero3`，不是 Zero 2/2W。
- [ ] 主板输入为稳定 5V。
- [ ] ReSpeaker 连接的是主板 USB-A Host，线材支持数据。
- [ ] Micro-HDMI 与 Mini-HDMI 没有混用。
- [ ] 扬声器为 4Ω 5W、JST-PH 2.0。
- [ ] 裸板底部没有接触金属桌面或散落螺丝。
- [ ] 麦克风开孔没有被遮挡，扬声器没有正对麦克风。

## 烧录 microSD

使用 Orange Pi 官方为 **Orange Pi Zero 3** 提供的 64 位 Debian 12
Bookworm Server 镜像。文件名类似：

```text
Orangepizero3_*_debian_bookworm_server_linux6.1.31.7z
```

官方入口：

- <https://www.orangepi.org/html/hardWare/computerAndMicrocontrollers/service-and-support/Orange-Pi-Zero-3.html>
- <https://www.orangepi.org/orangepiwiki/index.php/Orange_Pi_Zero_3>

不要使用 Android、Radxa、Orange Pi Zero 2 或 Zero 2W 镜像。板型镜像包含
对应的 bootloader、设备树和内核，不能互换。

烧录步骤：

1. 解压 `.7z`，得到 `.img`。
2. 将 microSD 插入电脑读卡器。
3. 用 balenaEtcher 选择 `.img`、microSD 和 Flash。
4. 等待写入与校验完成后安全弹出。
5. 断电插卡，再给 Orange Pi 上电。

第一轮可以不接小屏，直接连接网线后从路由器查找 IP，再用 SSH 登录。登录
方式以下载页对应镜像的 release notes 为准。

## 首次启动

执行：

```bash
uname -m
tr -d '\0' </proc/device-tree/model
awk '/MemTotal/ {print}' /proc/meminfo
```

预期包含：

```text
aarch64
OrangePi Zero3
MemTotal: 约 2GB
```

然后设置网络、时区并更新：

```bash
sudo apt update
sudo apt full-upgrade -y
sudo timedatectl set-timezone Asia/Shanghai
sudo reboot
```

## 安装 BMO

```bash
git clone https://github.com/heyi6351-alt/bmo-codex-assistant.git
cd bmo-codex-assistant/voice_assistant

sudo ./deploy/install-orangepi-zero3.sh
sudo ./deploy/install-voice-models.sh
```

安装脚本会：

- 验证 ARM64 与 `OrangePi Zero3` 设备树型号；
- 创建专用 `bmo` Linux 用户；
- 安装音频、Python、Chromium、Xorg 和 800×480 显示依赖；
- 把运行文件安装到 `/opt/bmo/voice_assistant`；
- 创建 Python 虚拟环境；
- 安装 `/etc/tmpfiles.d/bmo.conf`，保证每次开机自动重建运行目录
  `/run/bmo`（`/run` 是 tmpfs，重启会清空；不重建会导致 `bmo-voice`
  开机起不来）；
- 写入 `/etc/X11/Xwrapper.config`，让 server 镜像上无显示管理器时
  kiosk 仍能以 `bmo` 身份启动 Xorg；
- 把 Codex 的可写工作区放在 `/var/lib/bmo/codex_workspace`（服务以
  `ProtectSystem=strict` 运行，`/opt` 是只读的）；
- 安装 systemd 服务，但不会代替所有者完成账号授权。

## 屏幕配置

默认文件 `/etc/bmo/display.env` 内容为：

```text
BMO_DISPLAY_MODE=800x480
```

屏幕驱动板正常提供 EDID 时，Xorg 会自动使用 800×480。启动脚本只在
`xrandr` 确实提供该模式时请求它，否则保留屏幕首选模式。

先启动表情服务：

```bash
sudo systemctl enable --now bmo-display bmo-kiosk
```

如果黑屏：

```bash
cat /sys/class/drm/*/status
journalctl -u bmo-kiosk -b --no-pager
```

先换普通 HDMI 显示器验证 Orange Pi 输出，再检查 Micro/Mini HDMI 线和
屏幕 5V 供电。

## ReSpeaker 验收

ReSpeaker Lite 通常出厂就是 USB Audio 固件。先检测，不要为了“更新”而
预先刷固件：

```bash
lsusb
arecord -l
aplay -l
```

列表中应出现 ReSpeaker、XMOS 或 XU316。然后：

```bash
sudo -iu bmo
cd /opt/bmo/voice_assistant
.venv/bin/python -m bmo_voice --list-audio-devices
```

编辑 `/etc/bmo/voice.env`，让下面的值能唯一匹配真实设备名：

```text
BMO_AUDIO_DEVICE=ReSpeaker
```

只有在 Linux 完全检测不到 USB 声卡，或确认设备被刷成 I²S 固件时，才按
Seeed 官方指南刷 USB 固件。目标至少为 USB v2.0.5，仓库当前提供的新版为
v2.0.7：

<https://github.com/respeaker/ReSpeaker_Lite>

## Codex、GitHub 与飞书授权

以下步骤由 BMO 所有者执行，硬件开发不接触 token、appSecret 或用户授权
文件。

```bash
sudo -iu bmo
curl -fsSL https://chatgpt.com/codex/install.sh \
  | CODEX_NON_INTERACTIVE=1 sh
codex login --device-auth
codex login status
gh auth login
gh auth status
```

`gh` 只用于把确认过的 `bmo/<job-id>` 分支推送到 GitHub 并创建 Pull
Request；BMO 不会自动合并 PR，也不会直接推送主分支。继续在 `bmo` 用户下
安装和授权飞书：

```bash
npm config set prefix "$HOME/.npm-global"
npm install -g @larksuite/cli
npx -y skills add larksuite/cli -g
lark-cli config init --new
lark-cli auth login --domain calendar,task,im
```

## 启动与自检

自检脚本分两段：**硬件验收**（决定退出码，逐项 `PASS`/`FAIL`）和**所有者
授权**（只打印 `NOTE`，不影响退出码）。因此硬件同学在所有者授权
Codex/GitHub/飞书之前，就能先把板子相关项跑成“0 failed”。

### 硬件同学（不需要任何账号）

编辑 `/etc/bmo/voice.env` 的 `BMO_AUDIO_DEVICE`（见上一节），启动屏幕服务，
再跑硬件自检：

```bash
sudo systemctl enable --now bmo-display bmo-kiosk
sudo ./deploy/check-orangepi-zero3-hardware.sh
```

只要最后一行是 “Hardware check: N passed, **0 failed**”，硬件即通过。剩下的
`NOTE`（`gh` 未登录、Codex/lark-cli 未安装、`bmo-voice` 未启动）是留给所有者
的待办，不算硬件失败。把脚本完整输出交给软件同学。

### BMO 所有者（完成账号授权后）

先按上面《Codex、GitHub 与飞书授权》授权，再检查运行配置并启动语音服务：

```bash
sudo -iu bmo
cd /opt/bmo/voice_assistant
set -a
. /etc/bmo/voice.env
set +a
.venv/bin/python -m bmo_voice --check
exit

sudo systemctl enable --now bmo-voice
sudo ./deploy/check-orangepi-zero3-hardware.sh
```

`--check` 需要运行环境，务必先 `source /etc/bmo/voice.env`，否则会报
`BMO_VOSK_MODEL must point to ...`。这一轮自检里 `bmo-voice` 应为 active，
授权相关的 `NOTE` 也会随之消失。

查看日志：

```bash
journalctl -u bmo-display -u bmo-kiosk -u bmo-voice -f
```

## 硬件验收

`check-orangepi-zero3-hardware.sh` 的 `PASS`/`FAIL` 覆盖下面前几项硬件条目；
带 Codex/GitHub/飞书的条目属于所有者授权后验证。务必**真正重启一次**再确认
第三方服务恢复——安装当次和首次启动同处一次开机，会掩盖开机重建类问题。

- [ ] 自检识别 `OrangePi Zero3`、aarch64 和约 2GB 内存。
- [ ] `lsusb` 识别 ReSpeaker/XMOS。
- [ ] ALSA 同时存在 ReSpeaker 录音和播放设备。
- [ ] HDMI 状态为 connected。
- [ ] 4.3 英寸屏显示完整 BMO 眼睛、嘴和底部状态栏。
- [ ] 扬声器低音量开始测试，无破音、无欠压重启。
- [ ] 说“BMO / 哔某”后状态切到 LISTENING。
- [ ] 中文、英文和中英混说各完成一次问答。
- [ ] BMO 播放时插话能够终止播放并重新监听。
- [ ] Codex 提问、coding 指令和飞书日程查询各验证一次。
- [ ] coding 完成后说“不发布”不会产生 commit、push 或 PR。
- [ ] 再次发起 coding，完成后说“确认发布”只创建 `bmo/<job-id>` 分支和
  Pull Request，屏幕显示 PR URL，主分支不发生变化。
- [ ] 至少 `sudo reboot` 一次，开机后 display、kiosk、voice 三项服务全部
  自动恢复（`systemctl is-active bmo-display bmo-kiosk bmo-voice` 均为
  active）。
- [ ] 连续运行两小时无 USB 掉线、重启或明显过热。

## 性能调优

Orange Pi Zero 3 的 H618 与旧 Radxa 的 RK3566 性能不同。先使用仓库默认的
多语言 `ggml-base.bin` 完成准确率测试：

- 如果中文准确但等待明显过长，再对比 `ggml-tiny.bin`；
- 唤醒模型继续使用小型 Vosk，不让唤醒前音频上传；
- 2GB 板只运行一个 Chromium kiosk；
- 大型 coding 仓库放在工位电脑，本板负责语音和任务转发。

不要在本板运行 Whisper large 或本地大语言模型。

## 给硬件开发的简短交接消息

> 主板是 Orange Pi Zero 3 2GB。只烧 Orange Pi Zero 3 官方 Debian 12
> Bookworm Server 镜像。主板 USB-C 接稳定 5V/3A；Micro-HDMI 接屏幕
> Mini-HDMI 驱动板；USB-A Host 接 ReSpeaker Lite；4Ω 5W 喇叭只接
> ReSpeaker SPK JST-PH 2.0。依次跑 `install-orangepi-zero3.sh` 和
> `install-voice-models.sh`，`systemctl enable --now bmo-display bmo-kiosk`
> 起屏，按 `--list-audio-devices` 把 `/etc/bmo/voice.env` 的
> `BMO_AUDIO_DEVICE` 设成能唯一匹配的名字，再跑
> `check-orangepi-zero3-hardware.sh`——只要“0 failed”硬件就通过，`gh`/Codex/
> 飞书的 `NOTE` 是所有者的事，别去登录任何账号。把完整输出交给软件同学；
> 语音服务由所有者授权后再启动。原型独立供电，稳定后再用固定 5V 电源在
> 外壳内统一分电。
