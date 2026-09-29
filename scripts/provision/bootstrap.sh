#!/usr/bin/env bash
# Bring a fresh x86_64 Ubuntu host to the point where MobileWorld can run.
#
# Cloud-agnostic: it only assumes Ubuntu 22.04/24.04 on x86_64 with /dev/kvm
# already exposed to this machine. See README.md in this directory for the
# provisioning commands that get you such a machine.
#
# Idempotent — safe to re-run.

set -euo pipefail

# Pinned so that every trial is attributable to an exact environment.
MW_COMMIT="${MW_COMMIT:-83e7b8fc75ebb6c4a098254a42999db5f4172666}"
# NOTE: the `latest` tag is v1.0 (2025-12-24), NOT the newest build. v1.2+ fixes
# iptables-NAT failures that upstream describes as causing "deadlocked container
# launches and silent eval failures" on 6.x kernels. Injecting a known bug into a
# noise measurement would be measuring the wrong thing, so we pin v1.4.
MW_IMAGE="${MW_IMAGE:-ghcr.io/tongyi-mai/mobile_world:v1.4}"
WORKDIR="${WORKDIR:-$HOME/mobileworld-stability}"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m!! %s\033[0m\n' "$*" >&2; exit 1; }

say "Host check"
[ "$(uname -m)" = "x86_64" ] || die "arch is $(uname -m); MobileWorld's image is linux/amd64 only"
[ -e /dev/kvm ] || die "/dev/kvm is missing. Nested virtualization is not enabled on this instance."
echo "kernel : $(uname -r)"
echo "cpus   : $(nproc)"
echo "mem    : $(awk '/MemTotal/{printf "%.0f GB", $2/1048576}' /proc/meminfo)"
echo "disk   : $(df -h --output=avail / | tail -1 | tr -d ' ') free on /"

# The container runs docker-in-docker; a 6.x kernel that dropped iptable_nat
# breaks it. v1.2+ auto-detects nft, but check anyway so a failure is loud.
if ! (lsmod | grep -q '^iptable_nat' || modprobe -n iptable_nat 2>/dev/null || command -v iptables-nft >/dev/null); then
  echo "warn: neither iptable_nat nor iptables-nft detected; 'mw env check' will tell you"
fi

say "Packages"
sudo apt-get update -qq
# python3-matplotlib is only needed by scripts/plots.py; everything else in
# this repository is standard library only.
sudo apt-get install -y -qq ca-certificates curl git jq cpu-checker python3-matplotlib >/dev/null
sudo kvm-ok || die "kvm-ok says hardware virtualization is unavailable"

if ! command -v docker >/dev/null; then
  say "Docker"
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  sudo chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update -qq
  sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin
  sudo usermod -aG docker "$USER" || true
fi
docker --version || sudo docker --version

if ! command -v uv >/dev/null; then
  say "uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

say "MobileWorld checkout -> $WORKDIR/vendor/MobileWorld @ ${MW_COMMIT:0:8}"
mkdir -p "$WORKDIR/vendor"
if [ ! -d "$WORKDIR/vendor/MobileWorld/.git" ]; then
  git clone https://github.com/Tongyi-MAI/MobileWorld.git "$WORKDIR/vendor/MobileWorld"
fi
git -C "$WORKDIR/vendor/MobileWorld" fetch --quiet origin
git -C "$WORKDIR/vendor/MobileWorld" checkout --quiet "$MW_COMMIT"
git -C "$WORKDIR/vendor/MobileWorld" status --short

say "Python deps"
cd "$WORKDIR/vendor/MobileWorld"
uv sync

if [ ! -f .env ]; then
  cp .env.example .env
  echo "wrote .env from .env.example — put API_KEY in it before running eval"
fi

say "Image: $MW_IMAGE  (~10.5 GB compressed, expect 10-25 min)"
sudo docker pull "$MW_IMAGE"
echo -n "digest: "
sudo docker image inspect "$MW_IMAGE" --format '{{index .RepoDigests 0}}'

say "Prerequisite check"
sudo -E "$(command -v uv)" run mw env check || true

cat <<EOF

Next:
  1. put your API_KEY into $WORKDIR/vendor/MobileWorld/.env
  2. smoke test (2 tasks x 2 trials — this checks the pipeline, not stability;
     see ENVIRONMENT.md before trusting any number that comes out of it):
       cd $WORKDIR
       python3 scripts/run_matrix.py --arm smoke --trials 2 \\
         --tasks CloseFlightModeTask,OpenFlightModeTask \\
         --agent-type agents/pinned_e2e_agent.py \\
         --model-name <model> --llm-base-url <url> \\
         --or-provider <provider> --or-quant <quant> \\
         --temperature 0 --seed 42 --image $MW_IMAGE

     Use the pinned agent, not a registered one: an unpinned OpenRouter model id
     can be served by different providers at different quantizations, and that
     variance is indistinguishable from the environment variance being measured.
     Confirm it took effect before trusting anything:
       grep -h "PINNED AGENT CONFIG" $WORKDIR/runs/smoke/*/*/*/thread_*.log
EOF
