## Why

dev.22 的对话与旧控制台使用不同外壳、颜色和滚动规则，出现会话标题不可见、提示图标变形、管理页排版拥挤。用户已批准以 Agent 为主的全站重构，并要求实际截图验收。

## What Changes

- 统一品牌为 MediaFlow · 一站式媒体自动化Agent，统一设计标记、主题、外壳、管理导航与设置。
- 首页提供会话历史、可读对话、紧凑输入及按需详情；Enter发送、Shift+Enter换行，中文输入法不误发送。
- 设置收纳许可、免责声明和版本详情；旧业务入口及数据源保持不变。
- 重做所有业务页面的标题、控件、状态、响应式及滚动层级，修复类型检查。

## Capabilities

### New Capabilities
- `agent-first-console`: Agent主导的全站导航、视觉和键盘交互。

### Modified Capabilities
无；对话恢复协议在既有 integrate-opencode-agent-workbench 内维护。

## Impact

影响控制台页面、品牌、样式与测试。dev.23本机更新，不替换框架，不打包、推送或远端更新，不启动设备、不解除安全停止，不改写会话、草稿和设备数据。
