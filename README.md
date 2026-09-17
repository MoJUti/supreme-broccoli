# 本地部署运维 agent：任务存储

Pi 源码位于相邻的 `pi-mono/`；本项目的数据位于相邻的 `deploy-agent-data/`，不写入 Pi 仓库。

运行 `python storage/init_storage.py` 可创建或检查数据目录与 SQLite 初始结构。脚本可重复运行；后续结构变更需要单独的迁移，不会自动覆盖已有数据。

`state.sqlite` 保存项目、目标服务器、部署包索引、任务、审批、观察值和任务事件。`packages/` 保存按 SHA-256 标识的原始部署包；`evidence/` 保存子 agent 返回的证据；`reports/` 保存部署与运维文档。数据库中的文件路径均相对于 `deploy-agent-data/`。

Pi 的聊天会话由 Pi 自己管理，保存在 `pi-sessions/`；`tasks.pi_session_id` 只保存关联 ID。服务器检查值必须带采集时间；执行变更前重新检查现状。`task_events` 为追加式记录，不能作为数据库管理员级防篡改保证。

VPN 和 SSH 密码、令牌不写入此目录。`credential_ref` 只记录凭据标识；实际凭据由 Windows 凭据存储或交互登录管理。

备份运行中的数据库时应使用 SQLite 备份接口，并连同 `packages/`、`evidence/`、`reports/` 一起备份。`backups/` 是本地临时备份目录；重要数据还需复制到另一块设备。

## 当前只读入口

使用 `npm install --ignore-scripts` 安装 Pi SDK、`python -m pip install -r requirements.txt` 安装部署包解析依赖，然后运行：

```powershell
npm.cmd start -- --package D:\path\to\package.zip --connection D:\path\to\connection.json --prepare-only
```

`--prepare-only` 只登记部署包、发现连接配置并生成本地盘点，不连接服务器，也不调用模型。盘点会列出文件、体积，以及 Compose 中的服务、端口、依赖和环境变量名称；不会把环境变量值发给模型。去掉该选项后，Pi 主 agent 会使用唯一的 `task_context` 只读工具分析盘点；加 `--interactive` 可在首次分析后继续提问，以 `/exit` 结束。调用模型前需要为 Pi 配置登录或 API key。

连接信息文件目前支持 JSON，或每行 `键=值`、`键: 值` 的 UTF-8 文本。至少需要 `host`、`server` 或 `服务器地址`。可选字段包括 `project`、`ssh_user`、`vpn_type`、`vpn_profile`、`vpn_portal`、`vpn_username`、`vpn_password`、`ssh_password`。如果未指定 VPN 配置，程序会在连接信息文件的相邻目录寻找唯一的 `.ovpn` 或 `.vpn` 文件；找到多个时不会自行选择。EasyConnect 等客户端的连接器尚未实现，提供入口地址也不会让程序自动登录。

连接文件只由本地准备程序读取。盘点和 Pi 的工具结果仅记录是否提供了密码，不保存密码值。当前版本**尚未连接服务器或执行服务器预检**；数据库中的任务停留在 `prechecking`。不同格式的真实连接文件需要按实际字段扩展解析器。

`remote/child_agent.py` 是待安装在测试服务器上的一次性只读子 agent 原型。它只接受 `identity`、`system_snapshot` 和 `service_status` 三种结构化动作，不接受 shell 命令；目前尚未接入 SSH 传输，也没有安装到服务器。
