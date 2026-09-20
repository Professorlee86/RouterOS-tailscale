import os
import tarfile
import json
import hashlib
import io
import urllib.request
import subprocess

root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
scratch_dir = os.path.join(root_dir, "build_cache")
os.makedirs(scratch_dir, exist_ok=True)

import shutil
import zipfile

print("=== Building Tailscale Universal Image for ARM64 ===")

def ensure_upx():
    if shutil.which("upx"):
        return shutil.which("upx")
    local_upx = os.path.join(scratch_dir, "upx.exe")
    if os.path.exists(local_upx):
        return local_upx
    # Check parent scratch dir
    parent_scratch_upx = r"C:\Users\Administrator\.gemini\antigravity\brain\5fb40d52-ecb2-441a-9930-31e4ff736a4d\scratch\upx.exe"
    if os.path.exists(parent_scratch_upx):
        shutil.copy(parent_scratch_upx, local_upx)
        return local_upx
    print("Downloading UPX...")
    url = "https://github.com/upx/upx/releases/download/v4.2.4/upx-4.2.4-win64.zip"
    zip_path = os.path.join(scratch_dir, "upx.zip")
    urllib.request.urlretrieve(url, zip_path)
    with zipfile.ZipFile(zip_path, 'r') as z:
        for name in z.namelist():
            if name.endswith("upx.exe"):
                with open(local_upx, "wb") as f:
                    f.write(z.read(name))
                break
    try: os.remove(zip_path)
    except: pass
    return local_upx

upx_exe = ensure_upx()

# 1. Download Alpine minirootfs arm64 if not present
alpine_url = "https://dl-cdn.alpinelinux.org/alpine/v3.19/releases/aarch64/alpine-minirootfs-3.19.1-aarch64.tar.gz"
alpine_tgz = os.path.join(scratch_dir, "alpine_arm64.tar.gz")
if not os.path.exists(alpine_tgz):
    print("Downloading Alpine arm64...")
    urllib.request.urlretrieve(alpine_url, alpine_tgz)

# 2. Download Tailscale arm64 if not present
ts_url = "https://pkgs.tailscale.com/stable/tailscale_1.58.2_arm64.tgz"
ts_tgz = os.path.join(scratch_dir, "tailscale_1.58.2_arm64.tgz")
if not os.path.exists(ts_tgz):
    print("Downloading Tailscale arm64...")
    urllib.request.urlretrieve(ts_url, ts_tgz)

ts_daemon = os.path.join(scratch_dir, "tailscaled_arm64")
ts_cli = os.path.join(scratch_dir, "tailscale_arm64")

with tarfile.open(ts_tgz, "r:gz") as t:
    for m in t.getmembers():
        if m.name.endswith("/tailscaled"):
            with open(ts_daemon, "wb") as f:
                f.write(t.extractfile(m).read())
        elif m.name.endswith("/tailscale"):
            with open(ts_cli, "wb") as f:
                f.write(t.extractfile(m).read())

# UPX compress
if upx_exe and os.path.exists(upx_exe):
    print("Compressing tailscaled & tailscale with UPX...")
    subprocess.run([upx_exe, "--best", "--lzma", ts_daemon], capture_output=True)
    subprocess.run([upx_exe, "--best", "--lzma", ts_cli], capture_output=True)

# 3. Compile tcp_forwarder
tcp_fwd_src = os.path.join(root_dir, "src", "tcp_forwarder.rs")
tcp_fwd_bin = os.path.join(scratch_dir, "tcp_forwarder_arm64")
print("Compiling tcp_forwarder for aarch64-unknown-linux-musl...")
subprocess.run([
    "rustc", "--target", "aarch64-unknown-linux-musl",
    "-C", "linker-flavor=ld.lld", "-C", "linker=rust-lld",
    "-C", "opt-level=z", "-C", "lto=yes", "-C", "panic=abort",
    "-C", "strip=symbols", "-C", "codegen-units=1",
    tcp_fwd_src, "-o", tcp_fwd_bin
], check=True)

if os.path.exists(upx_exe):
    subprocess.run([upx_exe, "--best", "--lzma", tcp_fwd_bin], capture_output=True)

# 4. Extract dependencies from alpine
busybox_bytes, certs_bytes, musl_bytes = None, None, None
with tarfile.open(alpine_tgz, "r:gz") as t:
    for m in t.getmembers():
        if m.name.endswith("bin/busybox"):
            busybox_bytes = t.extractfile(m).read()
        elif m.name.endswith("etc/ssl/certs/ca-certificates.crt"):
            certs_bytes = t.extractfile(m).read()
        elif m.name.endswith("lib/ld-musl-aarch64.so.1") and m.size > 0:
            musl_bytes = t.extractfile(m).read()

# 5. Build Layer
uni_layer = os.path.join(scratch_dir, "uni_layer_arm64.tar")
uni_tar = os.path.join(root_dir, "tailscale_universal_arm64.tar")

dummy_script = b"#!/bin/sh\nexit 0\n"
entrypoint_script = b"""#!/bin/sh
set -e
export TS_DEBUG_RESOLV_CONF=none

mkdir -p /dev/net
if [ ! -c /dev/net/tun ]; then
    mknod /dev/net/tun c 10 200 || true
    chmod 600 /dev/net/tun || true
fi

mkdir -p /var/lib/tailscale /var/run/tailscale

echo "[port-forwarder] Starting TCP forwarders..."
/usr/local/bin/tcp_forwarder &

if [ -n "$TS_AUTHKEY" ]; then
    (
        while [ ! -S /var/run/tailscale/tailscaled.sock ]; do
            sleep 1
        done
        sleep 2
        HOSTNAME_FLAG=""
        if [ -n "$TS_HOSTNAME" ]; then
            HOSTNAME_FLAG="--hostname=$TS_HOSTNAME"
        fi
        /usr/local/bin/tailscale up --authkey="$TS_AUTHKEY" $HOSTNAME_FLAG --accept-routes=false --accept-dns=false
        echo "[tailscale-auto-auth] Tailscale registration finished!"
    ) &
fi

echo "[tailscale-core] Starting tailscaled..."
exec /usr/local/bin/tailscaled --state=/var/lib/tailscale/tailscaled.state --socket=/var/run/tailscale/tailscaled.sock --tun=kernel
"""

with tarfile.open(uni_layer, "w") as tar:
    def add_dir(name):
        ti = tarfile.TarInfo(name=name)
        ti.type = tarfile.DIRTYPE
        ti.mode = 0o755
        tar.addfile(ti)

    def add_file(name, data, mode=0o644):
        ti = tarfile.TarInfo(name=name)
        ti.size = len(data)
        ti.mode = mode
        tar.addfile(ti, io.BytesIO(data))

    def add_symlink(name, target):
        ti = tarfile.TarInfo(name=name)
        ti.type = tarfile.SYMTYPE
        ti.linkname = target
        ti.mode = 0o777
        tar.addfile(ti)

    for d in ["bin", "lib", "sbin", "usr", "usr/local", "usr/local/bin", "etc", "etc/ssl", "etc/ssl/certs", "var", "var/lib", "var/lib/tailscale", "var/run", "var/run/tailscale", "dev", "dev/net"]:
        add_dir(d)

    add_file("bin/busybox", busybox_bytes, 0o755)
    for applet in ["sh", "chmod", "mkdir", "sleep", "kill", "rm", "mknod", "cat", "echo", "ps", "ip"]:
        add_symlink(f"bin/{applet}", "busybox")

    add_file("lib/ld-musl-aarch64.so.1", musl_bytes, 0o755)
    add_symlink("lib/libc.musl-aarch64.so.1", "ld-musl-aarch64.so.1")

    for s in ["sbin/iptables", "sbin/ip6tables", "sbin/iptables-save", "sbin/iptables-restore", "sbin/ip6tables-save", "sbin/ip6tables-restore"]:
        add_file(s, dummy_script, 0o755)

    add_file("etc/ssl/certs/ca-certificates.crt", certs_bytes, 0o644)
    add_file("etc/resolv.conf", b"nameserver 1.1.1.1\nnameserver 8.8.8.8\n", 0o644)

    with open(ts_daemon, "rb") as f:
        add_file("usr/local/bin/tailscaled", f.read(), 0o755)
    with open(ts_cli, "rb") as f:
        add_file("usr/local/bin/tailscale", f.read(), 0o755)
    with open(tcp_fwd_bin, "rb") as f:
        add_file("usr/local/bin/tcp_forwarder", f.read(), 0o755)

    add_file("entrypoint.sh", entrypoint_script, 0o755)

with open(uni_layer, "rb") as f:
    layer_sha = hashlib.sha256(f.read()).hexdigest()

config_obj = {
    "architecture": "arm64",
    "os": "linux",
    "config": {
        "Entrypoint": ["/entrypoint.sh"],
        "Env": [
            "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "TS_DEBUG_RESOLV_CONF=none"
        ],
        "WorkingDir": "/"
    },
    "rootfs": {
        "type": "layers",
        "diff_ids": [f"sha256:{layer_sha}"]
    }
}
config_str = json.dumps(config_obj, indent=2)
config_sha = hashlib.sha256(config_str.encode('utf-8')).hexdigest()

manifest_obj = [
    {
        "Config": f"{config_sha}.json",
        "RepoTags": ["tailscale-ros:arm64"],
        "Layers": ["layer.tar"]
    }
]

with tarfile.open(uni_tar, "w") as tar:
    m_bytes = json.dumps(manifest_obj, indent=2).encode('utf-8')
    m_info = tarfile.TarInfo(name="manifest.json")
    m_info.size = len(m_bytes)
    m_info.mode = 0o644
    tar.addfile(m_info, io.BytesIO(m_bytes))

    c_bytes = config_str.encode('utf-8')
    c_info = tarfile.TarInfo(name=f"{config_sha}.json")
    c_info.size = len(c_bytes)
    c_info.mode = 0o644
    tar.addfile(c_info, io.BytesIO(c_bytes))

    l_info = tar.gettarinfo(uni_layer, arcname="layer.tar")
    with open(uni_layer, "rb") as f:
        tar.addfile(l_info, f)

print(f"ARM64 image created successfully: {uni_tar}, size: {os.path.getsize(uni_tar) / 1024 / 1024:.2f} MB")
