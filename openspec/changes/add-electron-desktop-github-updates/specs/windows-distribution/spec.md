## MODIFIED Requirements

### Requirement: 更新安全的数据目录
安装版 SHALL 把可变数据保存到程序目录之外，读取既有bootstrap及用户选择的数据根，默认使用当前用户MediaFlow数据目录。程序升级 MUST NOT 覆盖或删除数据库、截图、日志、设备档案、预设和凭据，不隐式迁移目录。

#### Scenario: 更新程序版本
- **WHEN** NSIS或旧版兼容安装更新程序文件
- **THEN** 继续使用原数据根，程序文件与用户数据分离，默认数据根为 `%LocalAppData%\MediaFlow\data`

### Requirement: 可安装和可卸载
系统 SHALL 生成每用户NSIS安装器和稳定快捷方式；安装/更新 SHALL 注册当前用户独立Python后台任务，卸载 SHALL 确认空闲并准确停机后移除该实例后台，默认保留运行数据。旧版Velopack迁移使用已核实的旧卸载入口。

#### Scenario: 首次安装
- **WHEN** 用户运行MediaFlow NSIS安装器
- **THEN** 安装至选择的程序目录、创建稳定Electron入口并能启动完整平台

#### Scenario: 卸载
- **WHEN** 用户卸载MediaFlow且该实例无业务、维护或人工占用
- **THEN** 安全停止并移除该实例后台及程序入口，运行数据保持不变

## ADDED Requirements

### Requirement: Electron发行与不可变GitHub更新资产
Electron发行 SHALL 包含自包含业务载荷、Skill和许可，使用NSIS安装及纯版本标签、更新清单、blockmap和哈希；每个版本绑定唯一干净提交，旧包不覆盖。

#### Scenario: 发布开发更新
- **WHEN** 发布新的Electron开发测试包
- **THEN** 对应dev频道可发现与校验完整EXE，差分资源可用且不包含凭据、设备数据或私人镜像

### Requirement: 旧版迁移与隔离升级验收
安装器 SHALL 核对旧版安装身份，旧停机及卸载完成后才注册新版后台，保留数据和程序恢复资料；验收配置 SHALL 隔离目录、数据、端口、托管及注册，跨更新保留。

#### Scenario: 迁移失败
- **WHEN** 旧版占用、实际停止失败或新程序验证失败
- **THEN** 不继续覆盖或删除数据，给出失败原因与程序恢复路径

#### Scenario: 本机独立验收
- **WHEN** 以验收profile执行安装及GitHub升级
- **THEN** 只修改该实例，开发服务和现有手机任务不被操作，模拟检查与真实安装分别记录
