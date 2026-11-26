"""Use reachable distribution mirrors before bootstrapping agent dependencies."""

MIRROR_COMMAND = r"""set -eu
if [ -f /etc/os-release ]; then
    . /etc/os-release
    if [ "${ID:-}" = ubuntu ]; then
        for source in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do
            [ -f "$source" ] || continue
            sed -E -i \
                's|https?://([a-zA-Z0-9-]+\.)?archive\.ubuntu\.com/ubuntu/?|http://repo.huaweicloud.com/ubuntu/|g; s|https?://security\.ubuntu\.com/ubuntu/?|http://repo.huaweicloud.com/ubuntu/|g' \
                "$source"
        done
    elif [ "${ID:-}" = fedora ]; then
        mirror=http://mirrors.aliyun.com/fedora
        # Fedora 42 and older releases have moved out of the live mirror.
        if [ "$VERSION_ID" -le 42 ]; then
            mirror=http://mirrors.aliyun.com/fedora-archive
        fi
        for source in /etc/yum.repos.d/fedora.repo /etc/yum.repos.d/fedora-updates.repo /etc/yum.repos.d/fedora-updates-testing.repo; do
            [ -f "$source" ] || continue
            sed -E -i \
                -e 's@^([[:space:]]*)(metalink|mirrorlist)=@#\1\2=@' \
                -e "s|^#baseurl=https?://download.example/pub/fedora/linux/|baseurl=$mirror/|" \
                "$source"
        done
    fi
fi
"""


async def configure_package_mirrors(agent, environment) -> None:
    # HTTP also works in minimal images before ca-certificates is installed.
    # Package managers still verify signatures with the distribution signing keys.
    await agent.exec_as_root(environment, command=MIRROR_COMMAND)
