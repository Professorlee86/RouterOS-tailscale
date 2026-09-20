# RouterOS-Tailscale

专为 **MikroTik RouterOS 7** 深度定制的超轻量、低内存占用、自带底层端口穿透与多节点隔离的 Tailscale 自动化组网方案。

同时支持 **ARM64**（如 hAP ax²、ax³、RB5009、CCR2004 等）与 **x86_64**（如 PC 软路由、PVE / ESXi 虚拟机、CHR 等）。

---

## 🌟 核心特性与解决痛点

1. **解决内网 IP 冲突（独立 Tailscale 虚拟 IP）**
   * 多台分支 ROS 设备内网通常都是相同的 `172.16.0.0/16` 或 `192.168.88.0/24` 网段。若所有设备都宣告子网路由，必然引发严重的 IP 冲突。
   * 本项目为每台 RouterOS 分配**专属独立的 Tailscale 虚拟 IP（`100.x.y.z`）**，设备间彻底隔离，互不干扰。

2. **自带底层 TCP 端口转发（无感直通 RouterOS 本体）**
   * 针对 RouterOS 容器与主机网络隔离的特点，内置了高性能 Rust 极简底层转发引擎（仅 174KB）。
   * 自动穿透 **WinBox (8888 / 8291)**、**WebFIG (80 / 443)**、**SSH (22)**、**API (8728)**。
   * **支持 WinBox 图形界面动态修改端口**：在 WinBox 的 `Container -> Envs` 中直接修改 `FORWARD_PORTS` 变量即可实时增减映射端口，无需进命令行！

3. **极致体积压缩（绝不撑爆 Flash 闪存）**
   * 官方 Tailscale 镜像解压需 150MB+，而 hAP ax² 等设备内置存储仅 128MB（平时可用仅 40~50MB），官方镜像直接爆盘。
   * 本项目通过裁剪冗余组件 + UPX 极致压缩，镜像仅 **21.4 MB**（x86 为 23.3 MB），解压后仅占 30MB 左右，留有充足安全余量。

4. **双架构与全版本自适应**
   * **全自动架构探测**：RouterOS 部署脚本自动识别 CPU 是 `arm64` 还是 `x86_64` 并拉取对应镜像。
   * **语法全自适应**：完美兼容 ROS 7.24+ 新语法（`list=ts_envs`）与 ROS 7.12~7.17 旧语法（`name=ts_envs`）。
   * **彻底杜绝内核冲突**：默认预设 `TS_DEBUG_RESOLV_CONF=none` 与 `--accept-dns=false`，杜绝 Linux 内核 inotify 对 `/etc/resolv.conf` 的监听 panic 故障。

---

## 🚀 极速部署指南（10 秒一键执行）

打开目标 RouterOS 的 **WinBox -> Terminal（终端）**，整段复制以下脚本粘贴执行即可：

```routeros
{
# =========================================================
# RouterOS Tailscale 全架构自动识别一键部署脚本
# =========================================================

# 1. 基础配置（按需修改 AuthKey 与节点名称）
:local tsAuthKey "tskey-auth-kzaGLUqPz911CNTRL-Cn3yR56K5xBj5CH4uiDRxBywXDWN6gWH"
:local tsHostName "MikroTik-Node"
:local forwardPorts "8888,8291:8888,80,443,22,8728"

# 2. 两个架构已验证的高速下载直链 (支持 AList / OSS / HTTP / HTTPS)
:local urlArm64 "http://home.lcwl.info:5244/d/B/ROS/tailscale_universal_arm64.tar?sign=Bru64Z23zxvB1ayKJ0XTd4QQQJIoUfW0tX_AwICn3-c=:0"
:local urlX86 "http://home.lcwl.info:5244/d/B/ROS/tailscale_universal_x86.tar?sign=HBNB5liQPl20HnqmZIPsgJZwbZJmCScazEk8_NQhCUk=:0"

# 3. 自动探测系统 CPU 架构并匹配下载链接
:local arch [/system/resource/get architecture-name]
:put ("[*] 检测到当前系统硬件架构: " . $arch)

:local imgUrl ""
:if ($arch ~ "arm64" || $arch ~ "aarch64") do={
    :set imgUrl $urlArm64
    :put ("[*] 成功匹配 -> 自动选择 ARM64 极简镜像")
} else={
    :if ($arch ~ "x86" || $arch ~ "amd64" || $arch ~ "chr") do={
        :set imgUrl $urlX86
        :put ("[*] 成功匹配 -> 自动选择 x86_64 极简镜像")
    } else={
        :error ("[-] 错误：当前系统架构 " . $arch . " 暂不支持，已终止！")
    }
}

# 4. 自动探测 LAN 网桥接口
:local lanBridge "bri_lan"
:do {
    :local b [/ip/address/get [find address~"172.16."] interface]
    :if ([:len $b] > 0) do={ :set lanBridge $b }
} on-error={}
:if ([:len [/interface/bridge/find name=$lanBridge]] = 0) do={
    :do { :set lanBridge [/interface/bridge/get [find] name] } on-error={}
}
:put ("[*] 匹配使用的 LAN 网桥: " . $lanBridge)

# 5. 创建虚拟网卡 veth-ts (防重复添加)
:if ([:len [/interface/veth/find name=veth-ts]] = 0) do={
    /interface/veth/add name=veth-ts address=172.16.0.3/16 gateway=172.16.0.1
    :put ("[*] 已创建虚拟网卡 veth-ts (172.16.0.3)")
}

# 6. 将虚拟网卡绑定到 LAN 网桥
:if ([:len [/interface/bridge/port/find interface=veth-ts]] = 0) do={
    /interface/bridge/port/add bridge=$lanBridge interface=veth-ts
    :put ("[*] 已将 veth-ts 加入网桥: " . $lanBridge)
}

# 7. 添加 Tailscale 静态回程路由
:if ([:len [/ip/route/find dst-address="100.64.0.0/10"]] = 0) do={
    /ip/route/add dst-address=100.64.0.0/10 gateway=172.16.0.3 comment="Tailscale Route"
    :put ("[*] 已添加 100.64.0.0/10 回程路由")
}

# 8. 写入环境变量 (兼容 7.24+ list= 与旧版 name=)
:put ("[*] 配置容器环境变量...")
:do {
    /container/envs/remove [find name=ts_envs]
} on-error={
    :do { /container/envs/remove [find list=ts_envs] } on-error={}
}
:do {
    /container/envs/add list=ts_envs key=TS_AUTHKEY value=$tsAuthKey
    /container/envs/add list=ts_envs key=TS_HOSTNAME value=$tsHostName
    /container/envs/add list=ts_envs key=FORWARD_PORTS value=$forwardPorts
} on-error={
    /container/envs/add name=ts_envs key=TS_AUTHKEY value=$tsAuthKey
    /container/envs/add name=ts_envs key=TS_HOSTNAME value=$tsHostName
    /container/envs/add name=ts_envs key=FORWARD_PORTS value=$forwardPorts
}

# 9. 满速下载对应镜像包
:put ("[*] 正在满速下载镜像，请稍候约 2~3 秒...")
/tool/fetch url=$imgUrl dst-path="tailscale_pkg.tar"

# 10. 创建容器 (自适应新版 envs 与旧版 envlist)
:put ("[*] 正在解压并创建容器...")
:do {
    /container/add file=tailscale_pkg.tar interface=veth-ts envlist=ts_envs start-on-boot=yes logging=yes
} on-error={
    /container/add file=tailscale_pkg.tar interface=veth-ts envs=ts_envs start-on-boot=yes logging=yes
}

# 11. 智能等待解压完成并启动容器
:put ("[*] 等待解压完成...")
:local cWait 0
:while ([:len [/container/find interface=veth-ts status=stopped]] = 0 && $cWait < 20) do={
    :delay 1s
    :set cWait ($cWait + 1)
}
/container/start [find interface=veth-ts]
:put ("[+] Tailscale 容器已成功启动并自动联网！")

# 12. 清理下载包释放存储空间
:delay 2s
/file/remove [find name="tailscale_pkg.tar"]
:put ("[+] 安装包已自动删除，存储空间已释放完毕！")
}
```

---

## 📡 远程管理连接

容器启动后约 3~5 秒内自动向 Tailscale 注册上线。在 [Tailscale 管理控制台](https://login.tailscale.com/admin/machines) 找到该设备并获取专属虚拟 IP（如 `100.x.y.z`）：

* **WinBox（自定义端口）**：输入 `100.x.y.z:8888`
* **WinBox（默认端口）**：直接输入 `100.x.y.z`（容器底层已自动将 8291 转发至 8888）
* **WebFig 网页管理**：浏览器打开 `http://100.x.y.z`
* **SSH 远程命令行**：`ssh admin@100.x.y.z`

---

## ⚙️ 动态端口穿透配置指南

如果后期需要增减端口或修改映射，**完全无需登录命令行**：

1. 在 WinBox 打开左侧 **`Container`** -> 切换到 **`Envs`** 标签页。
2. 找到 `ts_envs` 中的 **`FORWARD_PORTS`** 双击编辑：
   * **直接穿透**：例如追加 `,9000`（访问容器 9000 直接到 ROS 本体 9000）
   * **端口重定向**：例如 `8291:8888`（访问容器 8291 转发至 ROS 本体的 8888）
3. 点击 **OK** 保存。
4. 切回 **Containers** 列表，右键点击容器 -> 点击 **Stop** -> 再点击 **Start** 即可秒级生效！

---

## 🛠️ 项目结构与本地重新构建

```text
RouterOS-tailscale/
├── src/
│   └── tcp_forwarder.rs       # 极简高并发底层 TCP 端口转发器 (Rust 编写)
├── scripts/
│   ├── build_arm64.py         # ARM64 镜像构建与打包脚本
│   └── build_x86.py           # x86_64 镜像构建与打包脚本
├── routeros/
│   └── tailscale_deploy.rsc   # RouterOS 全架构自适应一键部署脚本
├── .gitignore
└── README.md
```

### 本地构建镜像
```bash
# 构建 ARM64 镜像
python scripts/build_arm64.py

# 构建 x86_64 镜像
python scripts/build_x86.py
```

---

## 📄 许可证

本项目基于 [MIT License](LICENSE) 开源。
