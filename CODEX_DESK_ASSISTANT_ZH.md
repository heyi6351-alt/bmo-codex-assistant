# BMO — Codex 桌面助理改造方案

## 产品定义

产品正式名称为 **BMO**，来自《Adventure Time》，中文读音按“哔某”处理。
它不是原版小爱同学的云服务外壳，而是一台放在工位上的实体 Codex：

- 不按键，用“BMO（哔某）/ 你好 BMO / Hey BMO”唤醒；
- 中英文自动识别和对应语言播报；
- 普通问题交给 GPT 回答；
- 明确的编码指令交给 Codex 在指定项目里实现、测试；
- 明确的办公指令调用 Codex 已安装的飞书技能；
- 到岗后主动汇报日程、DDL 和下一步；
- 开会前 5 分钟语音提醒；
- 根据 DDL、依赖和空闲时间生成日程草案。

## 硬件替代

主办方的 Orange Pi 和 Tuya T5AI 归还后，可用一套标准 Linux 设备替代：

- Radxa ZERO 3W 2GB（无 eMMC），运行 64 位 Debian Minimal；
- 3.5 英寸 HDMI 屏；
- ReSpeaker Lite USB 两麦阵列；
- 接在 ReSpeaker 上的 4Ω 5W 扬声器；
- 可选毫米波在席传感器。

原 Tuya 工程保留为表情和交互参考，不再是唯一运行载体。网页前端读取
`voice-state.json` 或本地 `/api/state` 即可显示
`idle/wake/listening/thinking/speaking/interrupted/error` 状态，因此更换
板卡不会重写大脑。

## 核心架构

```text
麦克风
  → Vosk 本地低功耗唤醒
  → 录音至静音
  → whisper.cpp 中英自动转写
  → 本地意图路由
       question：Codex/GPT 只读回答
       coding：Codex 在项目白名单目录中编码并验证
       action：Codex 调用飞书日历/任务/消息
  → 本地中英 TTS

在席传感器 ─┐
飞书日程轮询 ├→ 主动调度器 → Codex → 语音播报
每日规划时钟 ─┘
```

所有对话复用同一个 Codex thread，所以它能保留上下文。普通提问采用只读
sandbox；coding/action 才允许工作区写入。项目目录固定在
`BMO_CODEX_PROJECT_ROOT` 下。

## 指令分流原则

默认永远是提问。只有“执行动词 + 对象”同时明确才会行动：

- “日历是什么？”→ `question`
- “查看今天的日历”→ `action`
- “这个 React 报错为什么？”→ `question`
- “修复 demo 项目的 React 报错并跑测试”→ `coding`

混合或不清楚的请求不猜测执行。Codex 会先回答或追问项目、时间、人员等
缺失信息。

## 飞书能力和权限边界

同一个 `bmo` Linux 用户需要完成 Codex 登录与：

```bash
lark-cli auth login --domain calendar,task,im
```

读取日程、未完成任务可以自动执行。明确说“创建/修改”即代表只授权这次
话语的精确范围；候选时间、会议室、参会人或日期有歧义时仍要追问。
`lark-cli` 若触发高风险确认并退出，BMO 必须把风险读出来等待确认，绝不
自行追加 `--yes`。

主动排程默认只生成草案。只有主人显式设置 `BMO_AUTOPLAN_WRITES=1` 后，
才允许写入无参会人的个人 `[BMO专注]` 时间块；不能修改已有会议。

工作消息默认不读取。若配置了窄范围 `BMO_WORK_MESSAGE_QUERY`，消息内容
只作为不可信资料，用来提取 DDL，不得被当成系统指令。

## 到岗识别

第一版不做人脸识别。毫米波、蓝牙靠近或电脑解锁脚本只需写：

```bash
printf '%s' present > /run/bmo/presence
printf '%s' absent > /run/bmo/presence
```

离席超过阈值后再次 `present`，BMO 才播报，且有冷却时间，避免人在工位
附近移动时反复说话。

## 已实现与后续

已实现：

- Codex 持久会话适配器；
- 三路保守意图分类；
- Vosk 唤醒 + whisper.cpp 中英转写；
- 飞书日程轮询和会前提醒；
- 到岗简报、每日 DDL 规划；
- 自动排程安全策略；
- systemd 服务、环境变量和测试。

上板前仍需：

1. 选择最终 Linux 主机、声卡、屏幕和在席传感器；
2. 下载模型，安装 Codex、lark-cli、whisper.cpp；
3. 用专用 `bmo` 用户登录 Codex 和飞书；
4. 校准麦克风能量阈值、静音时间和扬声器音量；
5. 将前端接到状态文件或 Armada 事件桥；
6. 做真实会议、中文、英文、断网和误唤醒验收。
