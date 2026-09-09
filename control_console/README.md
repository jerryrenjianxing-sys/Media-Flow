# MediaFlow 控制台

Windows电脑端平台页面，默认端口3001；通过业务API48138操作，不直接连接设备。

## 路由

- /：外部Skill首页，复制完整Markdown和下载同源ZIP。
- /manage：任务台管理入口；/workbench：完整任务参数。
- /devices：设备与画面；/devices/guide：真机按需准备说明。
- /run：真实运行进度、等待与停止。
- /records：结果、异常和证据；/interactions：消息巡检与明确标记的历史。
- /content、/content/guide：主题、评论素材、预设、模型及填写说明。
- /governance：人工复核与证据治理。

## 开发与检查

Node要求>=22.13.0；依赖按package-lock.json安装，日常平台启动不临时安装或构建。

```powershell
npm ci
npm run typecheck
npm run lint
npm test
```

npm test包含生产构建及页面回归。仅构建通过不代表设备或实际浏览器流程通过。运行环境与入口见[运行手册](../RUNBOOK.md)；布局以[DESIGN](DESIGN.md)为准。
本轮只同步帮助文案，不恢复内置Agent、聊天状态或权限等级。
