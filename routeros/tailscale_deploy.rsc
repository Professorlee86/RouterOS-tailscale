{
# =========================================================
# RouterOS Tailscale 全架构自动识别一键部署脚本
# =========================================================

# 1. 基础配置（请修改此台设备的名称）
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

# 4. 自动探测 LAN 网桥接口 (优先获取 172.16.x.x 所在网桥，兜底使用 bri_lan 或 bridge)
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

# 8. 写入环境变量 (完美自适应 7.24+ 的 list= 与旧版 7.17- 的 name=)
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

# 9. 从 AList 满速拉取对应架构的镜像包
:put ("[*] 正在从 AList 满速下载镜像，请稍候约 2~3 秒...")
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
