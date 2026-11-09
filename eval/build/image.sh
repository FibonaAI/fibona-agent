#!/usr/bin/env bash
# Turn a fresh Ubuntu 22.04 ECS instance into a CPU worker like 10.42.2.111, then capture it as an image.
#
# Run as root on the build instance after `make image` has unpacked the reference bundle into
# /tmp/fibona-image: the reference worker's Docker debs, its sing-box binary and its registry login, plus the
# TUN-mode sing-box config rendered from sing-box.json. All outbound traffic goes through the TUN device except
# 10/8 (VPC: SSH, CR) and other private ranges; China IPs and Volcengine's 100.64/10 go direct, the rest through
# proxy-staff-relay. DNS is hijacked: China domains resolve at the VPC resolvers, the rest over DoT through the
# proxy, since the VPC resolvers return poisoned answers for blocked domains. The geoip-cn/geosite-cn rule sets
# are SagerNet's, refreshed daily by sing-box; the image keeps the copies cached while it was built. The proxy
# starts only where the instance metadata names a China region, so the same image serves regions with open
# internet access.
# The build instance needs no internet access. Afterwards stop it and create the image; instances
# started from that image (with a blank data disk, or none) are ready for jobs as is.
#
# Usage: bash image.sh "<harbor-runner public key>"; `bash image.sh bundle` as root on the reference
# worker writes that bundle to stdout as a tar stream.
set -euo pipefail

if [ "${1:-}" = bundle ]; then
    cd "$(mktemp -d)"
    mkdir debs
    cp /var/cache/apt/archives/{containerd.io,docker-ce,docker-ce-cli,docker-ce-rootless-extras,docker-buildx-plugin,docker-compose-plugin}_*.deb debs/
    cp -r /root/proxy-dev proxy-dev
    cp /etc/systemd/system/fibona-proxy.service /etc/apt/keyrings/docker.asc /etc/apt/sources.list.d/docker.list .
    cp /root/.docker/config.json docker-config.json
    tar c .
    rm -rf "$PWD"
    exit
fi

USER_NAME=harbor-runner
PUBKEY=${1:?Pass the public key for $USER_NAME}
BUNDLE=/tmp/fibona-image

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y rsync python3 tar
apt-get install -y "$BUNDLE"/debs/*.deb

# The data disk (1 TiB on the current workers) holds Docker's data. A new instance's disk is blank and
# has its own UUID, so format and mount it at boot instead of baking an fstab entry.
cat > /usr/local/sbin/fibona-data <<'EOF'
#!/bin/sh
set -eu
mkdir -p /data
disk=/dev/vdb
if [ -b "$disk" ] && ! mountpoint -q /data; then
    blkid "$disk" >/dev/null || mkfs.ext4 -q -L fibona-data "$disk"
    mount "$disk" /data
fi
mkdir -p /data/docker
EOF
chmod 755 /usr/local/sbin/fibona-data
cat > /etc/systemd/system/fibona-data.service <<'EOF'
[Unit]
Description=Mount the Fibona data disk
DefaultDependencies=no
After=local-fs.target
Before=containerd.service docker.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/fibona-data
[Install]
WantedBy=multi-user.target
EOF
for unit in docker containerd; do
    mkdir -p /etc/systemd/system/$unit.service.d
    printf '[Unit]\nRequires=fibona-data.service\nAfter=fibona-data.service\n' \
        > /etc/systemd/system/$unit.service.d/data.conf
done

# Same as the reference worker, plus two fixed addresses: docker0 stays 172.17.0.1 and Compose networks get
# 1024 /24s instead of the default ~30 networks.
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'EOF'
{
  "data-root": "/data/docker",
  "exec-opts": ["native.cgroupdriver=systemd"],
  "log-driver": "local",
  "bip": "172.17.0.1/16",
  "default-address-pools": [{"base": "172.20.0.0/14", "size": 24}]
}
EOF
# TUN-mode proxy for the host and, through auto_redirect, for containers; no per-program proxy settings.
install -d -m 700 /root/proxy-dev
install -m 700 "$BUNDLE"/proxy-dev/sing-box /root/proxy-dev/sing-box
install -m 600 "$BUNDLE"/proxy-dev/config.json /root/proxy-dev/config.json
install -m 644 "$BUNDLE"/fibona-proxy.service /etc/systemd/system/fibona-proxy.service
# Outside China regions the unit is skipped at boot and traffic goes out directly; on-failure, unlike the unit's
# Restart=always, leaves a skipped unit stopped.
mkdir -p /etc/systemd/system/fibona-proxy.service.d
cat > /etc/systemd/system/fibona-proxy.service.d/region.conf <<'EOF'
[Service]
Restart=on-failure
ExecCondition=/bin/sh -c 'curl -fsS -m 5 --retry 10 --retry-all-errors http://100.96.0.96/latest/region_id | grep -q "^cn-"'
EOF

# Docker's apt source and key, kept so `apt-get upgrade` behaves as on the reference worker.
install -d /etc/apt/keyrings
install -m 644 "$BUNDLE"/docker.asc /etc/apt/keyrings/docker.asc
install -m 644 "$BUNDLE"/docker.list /etc/apt/sources.list.d/docker.list

id "$USER_NAME" >/dev/null 2>&1 || useradd -m -u 2100 -d /var/lib/$USER_NAME -s /bin/bash "$USER_NAME"
usermod -aG docker "$USER_NAME"
echo "$USER_NAME ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-$USER_NAME
chmod 440 /etc/sudoers.d/90-$USER_NAME
install -d -m 700 -o "$USER_NAME" -g "$USER_NAME" /var/lib/$USER_NAME/.ssh
echo "$PUBKEY" > /var/lib/$USER_NAME/.ssh/authorized_keys
chown "$USER_NAME:$USER_NAME" /var/lib/$USER_NAME/.ssh/authorized_keys
chmod 600 /var/lib/$USER_NAME/.ssh/authorized_keys

# Registry login for both root and the runner user; drop the reference worker's container proxy settings.
python3 - "$BUNDLE"/docker-config.json <<'EOF'
import json, sys
config = json.load(open(sys.argv[1]))
config.pop("proxies", None)
json.dump(config, open(sys.argv[1], "w"))
EOF
install -d -m 700 /root/.docker
install -m 600 "$BUNDLE"/docker-config.json /root/.docker/config.json
install -d -m 700 -o "$USER_NAME" -g "$USER_NAME" /var/lib/$USER_NAME/.docker
install -m 600 -o "$USER_NAME" -g "$USER_NAME" "$BUNDLE"/docker-config.json /var/lib/$USER_NAME/.docker/config.json

# sync fills /opt/fibona per job; Harbor stages trials under /harbor.
mkdir -p /opt/fibona /harbor
chown "$USER_NAME" /opt/fibona

systemctl daemon-reload
systemctl enable fibona-data containerd docker fibona-proxy
systemctl restart fibona-data containerd docker
systemctl restart fibona-proxy

# What Harbor needs from a worker, checked as the runner user, plus internet access (through the proxy in China).
sudo -u "$USER_NAME" docker info --format '{{.CgroupVersion}} {{.CgroupDriver}} {{.DockerRootDir}}' | tee /dev/stderr \
    | grep -qx '2 systemd /data/docker' || { echo "Expected rootful Docker on cgroup v2 in /data/docker" >&2; exit 1; }
sudo -u "$USER_NAME" docker compose version
sudo -u "$USER_NAME" sudo -n true
for _ in $(seq 60); do curl -fsS -o /dev/null https://download.docker.com && break; sleep 2; done
curl -fsS -o /dev/null https://download.docker.com
echo "Internet OK"

# Leave nothing instance-specific in the image: bundle, Docker state, machine ID, cloud-init state, logs.
systemctl stop fibona-proxy docker docker.socket containerd
rm -rf "$BUNDLE" /data/docker/*
umount /data 2>/dev/null || true
apt-get clean
truncate -s 0 /etc/machine-id
cloud-init clean --logs --seed
echo "Ready: stop this instance and create the image (system disk only)."
