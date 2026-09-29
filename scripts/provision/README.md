# Getting a machine that can run MobileWorld

MobileWorld needs an **x86_64 Linux host with `/dev/kvm`**. Its CLI checks for
that device directly (`core/api/env.py:710`), its container image is
`linux/amd64` only, and the AVD inside is an `x86_64` system image driven by a
`linux-x64` emulator binary. Neither Apple Silicon nor any ARM instance works.

Two options, both with nested virtualization at no extra charge over the
instance price.

## GCP — `n2-standard-8` (recommended)

Step-by-step, including the quota and machine-family traps: **[gcp-walkthrough.md](gcp-walkthrough.md)**.

Nested virtualization is a per-instance flag. Requires Intel Haswell or later;
E2 and N2D are not eligible.

```bash
gcloud compute instances create mw-audit \
  --zone=us-central1-a \
  --machine-type=n2-standard-8 \
  --enable-nested-virtualization \
  --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud \
  --boot-disk-size=300GB --boot-disk-type=pd-balanced
```

```bash
gcloud compute ssh mw-audit --zone=us-central1-a
```

## AWS — `c8i.4xlarge`

Since February 2026 EC2 supports nested virtualization on virtual (non-metal)
instances, limited to the C8i / M8i / R8i families. Before that it needed a
`*.metal` instance, which costs several times more — so pick one of these three
families or you are paying for bare metal you don't need.

```bash
aws ec2 run-instances \
  --instance-type c8i.4xlarge \
  --image-id <ubuntu-24.04-amd64-ami> \
  --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=300,VolumeType=gp3}' \
  --key-name <your-key>
```

Nested virtualization must be enabled for the instance; check the current AWS
docs for the exact flag, as this is a recent feature.

## Sizing

| | why |
|---|---|
| **≥ 8 vCPU** | one Android emulator + Mattermost + Mastodon, all inside one container |
| **≥ 32 GB RAM** | same |
| **≥ 300 GB disk** | image is 10.5 GB compressed, ~30 GB unpacked, plus a screenshot per agent step across every trial |
| **Ubuntu 22.04 / 24.04** | what `bootstrap.sh` targets |

Screenshots are the disk hog: the pilot is roughly 8,200 agent steps per arm at
10 trials, and every step writes a PNG (plus a second annotated PNG for clicks
and drags).

## Then

```bash
curl -fsSL -o bootstrap.sh <this file's sibling>
bash bootstrap.sh
```

`bootstrap.sh` installs Docker and uv, checks out MobileWorld at the pinned
commit, pulls the pinned image, and runs `mw env check`. It is idempotent.

## Why the image is pinned to `v1.4`, not `latest`

`latest` and `v1` are the same digest, built 2025-12-24 — the *oldest* build,
not the newest. Upstream's `docs/docker_changelog.md` says v1.2 fixed
iptables-NAT failures that caused "deadlocked container launches and silent eval
failures" on 6.x kernels. Running v1.0 on a modern kernel would mix a known,
already-fixed bug into a measurement whose entire purpose is to characterise
noise. The trade-off is that most leaderboard entries predate v1.4, so the
environment is not byte-identical to theirs — which is recorded per row as
`image_digest` and stated in the report.
