# BMO Codex Assistant

一个放在工位上的实体 **BMO**（读作“哔某”）：无需按键，用中英文语音唤醒，
由 Codex 作为大脑回答问题、执行 coding 任务，并在明确授权后调用飞书日历、
任务和消息能力。

当前硬件目标已经统一为：

> **Orange Pi Zero 3 2GB + ReSpeaker Lite USB + 4.3 英寸 800×480 HDMI 屏**

比赛期间使用的 Orange Pi 3B 和 Tuya T5AI 不再是运行依赖。当前使用的是
团队自购的 Orange Pi Zero 3；原始比赛代码仍保留作历史参考，说明见
[`docs/ADVENTUREX_2026_LEGACY.md`](docs/ADVENTUREX_2026_LEGACY.md)。

## BMO 能做什么

- 识别 `BMO / 哔某 / 你好 BMO / Hey BMO`，不需要按键；
- 自动识别中文和英文；
- 普通问题交给 GPT/Codex，只返回语音答案，不执行工具；
- 明确的 coding 指令交给 Codex，在指定项目中修改代码并运行检查；
- 明确的功能指令按权限调用飞书，例如查看日程、创建任务和规划时间；
- 开会前五分钟语音提醒；
- 检测到你回到工位后，主动播报日程和待办摘要；
- 根据 DDL、任务和日历冲突生成专注时间规划；
- 播报期间保持监听，支持通过 ReSpeaker AEC 和能量检测打断；
- 在 HDMI 小屏显示待机、聆听、思考、说话和错误状态。

## 指令路由

```text
“哔某”
   │
   ▼
中英双语转写
   │
   ├── 普通问题 ──→ GPT / Codex 只读回答 ──→ 语音播报
   │
   ├── Coding 指令 → Codex 项目工作区 ────→ 修改 + 测试 + 汇报
   │
   └── 功能指令 ──→ 飞书日历 / 任务 / 消息 → 结果或确认
```

路由默认保守：没有明确执行动词时按“提问”处理。例如：

| 语音 | 路由 | 行为 |
|---|---|---|
| “日历是什么？” | 提问 | 解释概念，不调用飞书 |
| “查看我今天的日历” | 功能 | 读取当天日程 |
| “CSS subgrid 怎么用？” | 提问 | 返回技术解释 |
| “修改 demo 的登录页并跑测试” | Coding | 进入授权项目执行 |

## 系统结构

```text
Orange Pi Zero 3 2GB
  ├── USB → ReSpeaker Lite → 4Ω 5W 扬声器
  ├── Micro HDMI → Mini HDMI 驱动板 → 4.3" 800×480 BMO 表情屏
  └── Debian 12 Bookworm Server
        ├── 本地 Vosk 唤醒词
        ├── whisper.cpp 中英转写
        ├── BMO 意图路由与主动提醒
        ├── Codex CLI
        ├── lark-cli / 飞书 Skills
        └── 本地网页表情 + Chromium Kiosk
```

大语言模型推理通过网络完成，不在 2GB 板子上运行本地大模型。唤醒词检测在
本地进行，唤醒前不需要向云端发送持续音频。

## 从这里开始

### 1. 准备硬件

先阅读：

- [采购清单](CODEX_DESK_ASSISTANT_BOM_ZH.md)
- [Orange Pi Zero 3 接线、烧录与硬件验收](HARDWARE_BRINGUP_ORANGEPI_ZERO3_ZH.md)

### 2. 在板子上安装

```bash
git clone https://github.com/heyi6351-alt/bmo-codex-assistant.git
cd bmo-codex-assistant/voice_assistant

sudo ./deploy/install-orangepi-zero3.sh
sudo ./deploy/install-voice-models.sh
sudo ./deploy/check-orangepi-zero3-hardware.sh
```

### 3. 授权 Codex 与飞书

账号授权必须由 BMO 所有者完成，不要把 token 或密钥交给硬件开发。

```bash
sudo -iu bmo

curl -fsSL https://chatgpt.com/codex/install.sh \
  | CODEX_NON_INTERACTIVE=1 sh
codex login --device-auth

npm config set prefix "$HOME/.npm-global"
npm install -g @larksuite/cli
npx -y skills add larksuite/cli -g
lark-cli config init --new
lark-cli auth login --domain calendar,task,im
```

### 4. 检查并启动

根据真实声卡名称编辑 `/etc/bmo/voice.env`，然后运行：

```bash
sudo -iu bmo
cd /opt/bmo/voice_assistant
set -a
. /etc/bmo/voice.env
set +a
.venv/bin/python -m bmo_voice --check
exit

sudo systemctl enable --now bmo-display bmo-kiosk bmo-voice
```

查看运行日志：

```bash
journalctl -u bmo-display -u bmo-kiosk -u bmo-voice -f
```

## 仓库结构

| 路径 | 用途 |
|---|---|
| `voice_assistant/bmo_voice/` | 唤醒、录音、路由、Codex、飞书、提醒和 TTS |
| `voice_assistant/web/` | 本地 BMO HDMI 表情界面 |
| `voice_assistant/deploy/` | Orange Pi 安装、自检和 systemd 服务 |
| `voice_assistant/tests/` | 核心逻辑和显示服务测试 |
| `voice_assistant/codex_workspace/` | BMO 执行 coding 时的安全工作区规则 |
| `app/`, `board/`, `bmo_worker/`, `pc_agent/` | AdventureX 2026 历史实现 |

## 当前状态

| 模块 | 状态 |
|---|---|
| 中英唤醒、转写和意图路由 | 已实现并有自动化测试 |
| Codex 连续会话与 coding 路由 | 已实现 |
| 飞书日程、待办、会前提醒和日程草案 | 已实现 |
| 回到工位后的主动播报 | 已实现文件式 presence 接口 |
| AEC 辅助插话 | 已实现，需在最终外壳中调阈值 |
| 800×480 HDMI 表情页面 | 已实现响应式布局，等待真屏验收 |
| Orange Pi Zero 3 + ReSpeaker + 小屏整机 | 等待真实硬件验收 |

目前的自动规划默认只生成草案。只有显式设置
`BMO_AUTOPLAN_WRITES=1` 后，才允许创建无参会人的个人专注时间块；不会修改
已有会议。

## 本地测试

```bash
cd voice_assistant
PYTHONPATH=. python3 -m unittest discover -s tests -v
PYTHONPYCACHEPREFIX=/tmp/bmo-pycache \
  python3 -m compileall -q bmo_voice tests
```

详细软件说明见 [`voice_assistant/README.md`](voice_assistant/README.md)，工作拆分
见 [`CODEX_DESK_ASSISTANT_WORKLIST_ZH.md`](CODEX_DESK_ASSISTANT_WORKLIST_ZH.md)。
