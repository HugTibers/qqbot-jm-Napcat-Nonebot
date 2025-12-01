## JM 插件说明

基于 `jmcomic`，在 QQ 群/私聊里按专辑号下载禁漫并生成 PDF,发送到私聊或群聊。
![alt text](help/image.png)

### 指令
- `jm<id>`：下载漫画并发送 PDF（示例：`jm123456`）。
- `jm队列` / `jmqueue`：查看当前下载中 / 排队任务。
- `jm取消<id>` / `jm删除<id>`：取消等待队列中的指定漫画。
- `jmhelp` / `jm帮助`：查看帮助。

### 特性
- **权限**：
  - `ALLOWED_GROUPS` 为空时不限制群，填入群号集合则仅允许特定群。
- **上传**：
  - 上传超时自适应（文件越大超时越长，最高 300s）。
  - 遇到 `rich media transfer failed` / `retcode=1200` （大概率风控） 自动重传
  - 可能的上传超时会提示“上传耗时较长”。
- **清理**：上传后延迟删除下载目录（默认 `CLEANUP_DELAY_SECONDS=86400` 秒）。
- **存储**：默认下载路径按专辑ID分隔（`option.yml` 中 `rule: Bd / Aid / Ptitle`），避免不同漫画章节同名时被混合。

### 主要配置（`plugins/jmcomic/service.py`）
- `ALLOWED_GROUPS`: `{}` 表示不限制群；填 `{123456789, ...}` 仅允许特定群。
- `CLEANUP_DELAY_SECONDS`: 上传后延迟清理秒数，默认 `86400`。
- `MAX_CONCURRENT`: 同时下载/上传任务数上限，默认 `2`。
- `API_TIMEOUT`: 短接口默认超时（秒），默认 `20`。

### 详细部署教程
完整安装与运行步骤请见 [教程.md](help/教程.md)。

### Reference
- NapCat: https://napneko.github.io/guide/napcat
- JMComic: https://github.com/hect0x7/JMComic-Crawler-Python
- Nonebot: https://github.com/nonebot/nonebot2
