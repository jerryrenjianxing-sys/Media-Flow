# device-initialization Specification

## MODIFIED Requirements

### Requirement: 自动初始化后执行零写入自检
从标准模板创建或自动接管的虚拟机 SHALL 复用现有独立初始化；实例未登录时 MUST 停在 `waiting_user`，用户继续且初始化完整通过后再执行一轮3条视频的全零动作自检。自检必须保持点赞、收藏、评论概率为零并启用评论预览。

#### Scenario: 模板实例等待登录
- **WHEN** 自动流程在新虚拟机中识别到登录、身份或安全验证页面
- **THEN** 初始化进入 `waiting_user` 并通过受锁人工控制会话等待用户处理，不自动登录、不绕过验证且不提交自检

#### Scenario: 初始化与自检通过
- **WHEN** 用户继续后初始化进入 `ready`，且3条自检完整完成、模型有效、页面错误为零、写入计数为零
- **THEN** 系统把该虚拟机标记为自动接管已验证并允许加入任务草稿，但不自动提交正式任务

#### Scenario: 初始化或自检失败
- **WHEN** 初始化进入等待、失败、取消或陈旧状态，或者自检不是完整成功
- **THEN** 自动流水线停在对应阶段并保存原因，不重新初始化、不重新提交自检

#### Scenario: MediaFlow重启后继续等待
- **WHEN** 后台在 `waiting_user` 或自检前阶段重启
- **THEN** 系统按持久化初始化记录、Provider真实状态和当前页面恢复展示，不假定登录完成或自动越过断点

#### Scenario: 初始化环境没有模型Key
- **WHEN** 虚拟机已连接ADB但当前Windows用户尚未配置OpenRouter Key
- **THEN** 固定Worker仍可启动并完成不需要模型的连接、应用和UI检查；只有首次真正调用视觉模型时才进入“等待配置模型”，且不得清除已验证的ADB映射
