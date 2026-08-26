# 抖音固定执行器（内部测试原型）

用途：在已授权的内部隔离测试手机上，验证固定程序相对 AutoGLM 的速度、动作复核和停止策略。

当前流程：

1. 从当前推荐流上滑到一条新视频；
2. 连续浏览 4 条，默认停留 `4/5/4/5` 秒；
3. 第 2 条点赞并验证心形变红；
4. 第 3 条收藏并验证星形变黄；
5. 第 4 条打开评论区、验证面板出现，然后关闭；
6. 记录每一步截图、JSONL 日志、总耗时和验证开销。

安全机制：

- 每台设备有独占锁；
- 截图尺寸不是已校准的 `1080×2400` 时停止；
- 主推荐流前置状态无法确认时停止；
- 点赞或收藏原本已激活/视觉状态含糊时停止，避免反向取消；
- 动作后颜色状态未改变时停止，不继续执行后续状态改变动作；
- 不发送固定评论。语义评论应由独立的一次性模型调用生成。

运行：

```powershell
& 'C:\Users\jerry\.codex\skills\mobile-harness\.venv\Scripts\python.exe' `
  'C:\Users\jerry\Documents\Codex\2026-08-19\wo-c\fixed_runner\douyin_fixed_runner.py'
```

局限：当前视觉检查针对这台 OPPO 的抖音布局校准，是速度/架构原型，不是跨机型通用适配器。商业内容、直播、商品和特效识别尚需 OCR/视觉分类硬闸门。

## 第二版：uiautomator2 + 动作硬闸门

`douyin_uia2_runner.py` 直接使用 `uiautomator2 3.7.0`，保留相同的设备锁、截图、动作后复核和 JSONL 轨迹，并在点赞、收藏、打开评论区之前读取当前可见 UI 树。

硬闸门会阻止以下页面改变账号状态：

- 广告、推广、咨询和下载引导；
- 商品、购买、进店、领券和价格信号；
- 正在直播或进入直播间；
- 收藏特效、使用特效和道具详情；
- 缺少视频、喜欢、评论、收藏四类主视频流控件；
- 抖音不在前台、UI 树读取失败或主视频流视觉检查失败。

单独出现顶部导航中的“直播 / 团购 / 商城”或普通视频的“拍同款”不会触发拦截。命中硬闸门后程序上滑寻找下一条候选视频，默认每种动作最多跳过 3 条；仍找不到安全候选时停止。

运行第二版：

```powershell
& 'C:\Users\jerry\.codex\skills\mobile-harness\.venv\Scripts\python.exe' `
  'C:\Users\jerry\Documents\Codex\2026-08-19\wo-c\fixed_runner\douyin_uia2_runner.py'
```

运行全部离线测试：

```powershell
& 'C:\Users\jerry\.codex\skills\mobile-harness\.venv\Scripts\python.exe' `
  -m unittest discover -s 'C:\Users\jerry\Documents\Codex\2026-08-19\wo-c\fixed_runner' -p 'test_*.py'
```

## 长驻 Worker 与任务队列

当前推荐入口是 `worker.py`。它只连接手机一次，然后从本地 SQLite 队列依次领取任务；队列位于 `runtime/tasks.db`，每项任务的截图和日志位于 `runtime/artifacts/runs/`。

任务状态包括：

- `pending`：等待执行；
- `running`：已被某个 Worker 独占领取；
- `completed`：动作和复核完成；
- `failed`：安全停止，需要检查证据。

Worker 被异常终止时，遗留的 `running` 任务会标为失败，不会自动重试。这样可以避免某次点赞或评论其实已经成功，却因重跑而反向取消或重复发送。

Worker 启动时手机暂时离线不会退出；它会按间隔持续等待。领取每个已到期任务前还会做一次设备预检，预检失败时任务保持 `pending`，先重连手机，避免把未开始的任务误记为失败。任务执行中途断线仍按失败留证，不自动重做可能已经生效的互动。

队列支持确定性定时：批量任务会写入各自的最早执行时间，Worker 不会提前领取。失败任务会保留截图和错误，不会自动重做；Worker 会在下一项任务开始前重新连接设备。

非技术入口：

- 双击 `start-worker.cmd`：启动长驻 Worker；
- 双击 `set-openrouter-key.cmd`：隐藏输入并为当前 Windows 用户加密保存 OpenRouter 密钥；
- 双击 `start-worker-openrouter.cmd`：用 OpenRouter 的 Gemini 视觉模型启动 Worker；
- 双击 `submit-benchmark.cmd`：提交一次只看、点赞、收藏、评论区开关流程；
- 双击 `show-status.cmd`：查看最近任务状态；
- 双击 `show-report.cmd`：刷新并打开累计运行报告；
- 双击 `submit-healthcheck-batch.cmd`：提交 10 次只读健康检查，每分钟一次；
- 双击 `submit-comment-preview.cmd`：提交 AI 评论预览，不发送；
- `submit-comment.cmd`：只用于明确授权的内部测试，会在所有闸门通过后尝试发送一条评论。
- 双击 `submit-two-video-demo.cmd`：两视频演示；第一条点赞，第二条收藏并尝试生成、发送和复核一条评论。

评论模块使用现有 DPAPI 加密保存的 Z.AI 密钥。`run-worker-secure.ps1` 只在 Worker 进程内临时解密，密钥不会写入任务数据库或运行日志。手机动作模型仍是 `autoglm-phone-multilingual`；独立评论理解默认使用免费的通用视觉模型 `glm-4.6v-flash`，避免让动作专用模型承担自然语言评论生成。评论模型只能返回候选文本，不能点击手机；固定程序会再次检查长度、置信度、商业内容、联系方式和推广文本。模型选择跳过或校验失败时不发送。429/5xx 网络错误最多短暂重试一次，不会无限消耗额度。

OpenRouter 是独立可选路线：先运行 `set-openrouter-key.cmd`，再运行 `start-worker-openrouter.cmd`。该入口只把评论截图理解切换为 `google/gemini-3.1-flash-lite`，手机连接、点击、闸门和动作后验证仍由本地固定程序负责。这个任务只需要看一张截图并返回很短的结构化 JSON，因此默认选择成本更低的 3.1 Flash Lite；不会为此默认使用更重的 Gemini 3.7 Flash。Google 模型发生限流或服务错误时，OpenRouter 会自动回退到不同厂商的 `openai/gpt-4.1-nano`。请求强制严格 JSON Schema，并要求供应商支持全部参数且禁止选择会收集数据的路由端点。当前电脑的 iKuuuVPN 没有启用 Windows 系统代理，OpenRouter 启动器仅为自身设置 `http://127.0.0.1:7890`，不会修改其他程序的网络。OpenRouter 和 Z.AI 的密钥分别加密保存，互不覆盖。

当前评论发送路径已完成本地模拟测试，但将具体抖音截图发送给外部 Z.AI 前仍需要针对该数据流的明确授权。没有该授权时只能运行健康检查和普通固定流程。

命令行定时示例：

```powershell
& 'C:\Users\jerry\.codex\skills\mobile-harness\.venv\Scripts\python.exe' `
  '.\fixed_runner\worker.py' submit healthcheck --count 20 --interval-seconds 300
```

互动按钮不再使用固定坐标。每次动作前都会从当前 UI 树取得“喜欢 / 收藏 / 评论”的可点击区域并点击中心，评论区使用语义“关闭”按钮退出；这可以适应不同视频导致的按钮上下偏移。颜色复核也跟随本次按钮区域。

当前硬闸门仍是保守的 UI 文字与结构规则。没有可见文字的纯画面商业内容仍可能漏判，因此在扩大设备数量前应继续收集误判样本；运行截图会持续占用磁盘，正式长期保留周期需由风控审计要求决定。
