# GCP: from zero to a host that runs MobileWorld

Everything here is done in the browser. You do not need to install `gcloud`
locally — Cloud Shell has it.

Total: about 20 minutes of clicking, then ~30 minutes of unattended downloading.

---

## 0. Before you start — the two things that go wrong

**Do not pick an `e2-*` or `n2d-*` machine type.** Nested virtualization is
unavailable on E2 and on N2D (AMD). It needs an Intel Haswell-or-later host,
which in practice means `n2-*`, `n1-*` or `c3-*`. Getting this wrong is silent:
the VM boots fine and `/dev/kvm` simply isn't there.

**Nested virtualization is not a checkbox you can rely on finding in the
console.** It is a flag at creation time. Create the instance from Cloud Shell
with `--enable-nested-virtualization` and you are done in one command.

---

## 1. Project and billing

1. Go to <https://console.cloud.google.com>.
2. Top bar → project dropdown → **New Project**. Name it `mobileworld-audit`.
   Note the **Project ID** it generates (something like `mobileworld-audit-123456`)
   — that is what commands use, not the display name.
3. Left menu → **Billing** → link a billing account. A card is required even on
   the free trial.

## 2. Enable the API

Left menu → **APIs & Services** → **Enable APIs** → search "Compute Engine API"
→ **Enable**. First enable takes a minute or two.

## 3. Check your quota

New projects are often capped below 8 vCPUs in a region. Do **not** try to find
this in the console's quota table — it has thousands of rows and the filter is
fiddly. Ask for it directly from Cloud Shell (the `>_` icon, top-right):

```bash
gcloud config set project <YOUR_PROJECT_ID>
```

```bash
gcloud compute regions describe us-central1 --flatten="quotas[]" --format="csv[no-heading](quotas.metric,quotas.limit,quotas.usage)" | grep -E "^(CPUS|N2_CPUS|DISKS_TOTAL_GB),"
```

Two gcloud quirks are baked into that line: `describe` has no `--filter` (only
`list` commands do), and `--format="table(...)"` collapses to a vertical
`KEY: value` layout in a narrow terminal, which breaks grep. `csv` always emits
one record per line. Columns are metric, limit, usage.

You need `N2_CPUS` >= 8, `CPUS` >= 8, and `DISKS_TOTAL_GB` >= 300. There is also
a cross-region cap worth a glance:

```bash
gcloud compute project-info describe --flatten="quotas[]" --format="csv[no-heading](quotas.metric,quotas.limit,quotas.usage)" | grep CPUS_ALL_REGIONS
```

Honestly, the fastest path is to skip ahead and just run the create command in
step 4. When quota is the problem gcloud says so precisely — `Quota 'N2_CPUS'
exceeded. Limit: 0.0 in region us-central1.` — which names the exact metric to
request. Checking first only saves time if you already expect to be blocked.

To raise one: **IAM & Admin → Quotas & System Limits**, type `Name: N2 CPUs`
into the Filter box at the top of the table (pick the property, then the value —
it is not a column header), select the row, **Edit Quotas**. Approval is usually
minutes to a few hours.

## 4. Create the instance

In Cloud Shell:

```bash
gcloud compute instances create mw-audit \
  --zone=$ZONE \
  --machine-type=n2-standard-8 \
  --enable-nested-virtualization \
  --image-family=ubuntu-2404-lts-amd64 \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=300GB \
  --boot-disk-type=pd-balanced \
  --metadata=serial-port-logging-enable=TRUE
```

`ZONE_RESOURCE_POOL_EXHAUSTED` means that zone is temporarily out of that
machine type. It has nothing to do with your quota. Scan zones until one takes
it:

```bash
for M in n2-standard-8 n1-standard-8; do for Z in us-central1-b us-central1-c us-central1-f us-east1-b us-east1-c us-east1-d us-west1-a us-west1-b us-east4-a us-west4-a; do echo "--- $M @ $Z"; if out=$(gcloud compute instances create mw-audit --zone=$Z --machine-type=$M --enable-nested-virtualization --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud --boot-disk-size=300GB --boot-disk-type=pd-balanced 2>&1); then echo "SUCCESS  ZONE=$Z  MACHINE=$M"; break 2; fi; echo "$out" | grep -oE "code: [A-Z_]+|Quota '[A-Z_]+' exceeded[^.]*"; done; done
```

Capture gcloud's status with `if out=$(...)`, not by piping it. A pipeline's
exit status is the *last* command's, so `gcloud ... | tail -3 && echo OK` reports
success on every failure.

The loop falls back to **`n1-standard-8`** after exhausting N2. N1 supports
nested virtualization too and is far less contended; the emulator is bound by
KVM and memory, not by CPU generation.

Write down the zone and machine type that succeed — every later `ssh`, `stop`,
`scp` needs `--zone=<that one>`.

Do **not** work around this by switching to `e2-*` or `n2d-*`. Those create
successfully and then have no `/dev/kvm`, which you would discover after
pulling a 10.5 GB image.

> Every command below writes `$ZONE`. Set it once in your shell to the zone that
> worked: `export ZONE=us-east1-b` (substitute yours).

## 5. Confirm nested virtualization actually landed

This is the one check worth doing before installing anything.

```bash
gcloud compute ssh mw-audit --zone=$ZONE --command="ls -l /dev/kvm; lscpu | grep -ci vmx; nproc; free -g | head -2; df -h / | tail -1"
```

You want `/dev/kvm` to exist and the `vmx` count to be non-zero. The first ssh
generates a key — press Enter twice for an empty passphrase, or you will be
typing it on every later command. If `/dev/kvm` is
missing, the instance was created without the flag — delete it and redo step 4
rather than trying to fix it in place; nested virtualization cannot be turned on
for an existing instance's boot disk without recreating from a licensed image.

## 6. Run the bootstrap

```bash
gcloud compute ssh mw-audit --zone=$ZONE
```

Then on the instance:

```bash
curl -fsSL https://raw.githubusercontent.com/<this-repo>/main/scripts/provision/bootstrap.sh -o bootstrap.sh
```

Until this repository is published, just paste the file over instead:

```bash
cat > bootstrap.sh   # paste, then Ctrl-D
bash bootstrap.sh
```

It installs Docker and uv, checks out MobileWorld at the pinned commit, pulls
the pinned `v1.4` image (10.5 GB — this is the slow part), and runs
`mw env check`. Re-running it is safe.

Log out and back in once after the first run so your user picks up the `docker`
group.

## 7. Put the API key in place

```bash
nano ~/mobileworld-stability/vendor/MobileWorld/.env
```

Set `API_KEY=`. The other keys (`USER_AGENT_*`, `DASHSCOPE_API_KEY`,
`MODELSCOPE_API_KEY`) are only needed for the user-interaction and MCP suites,
which the pilot does not touch.

## 8. Smoke test

```bash
sudo uv run mw env run --count 1 --image ghcr.io/tongyi-mai/mobile_world:v1.4 --launch-interval 10
```

Watch it come up — the Android emulator boot alone takes several minutes, and
the container healthcheck polls `:6800/health`:

```bash
curl -s localhost:6800/health
```

Then the two-task smoke run printed at the end of `bootstrap.sh`.

---

## Money

`n2-standard-8` on-demand in `us-central1` is roughly **$0.39/hour**, plus about
**$0.10/GB-month** for the 300 GB balanced disk. Confirm against the pricing
calculator before you leave it running — regional prices differ and these
change.

The pilot is ~27 hours of wall clock, so on the order of **$11 of compute**.
That is smaller than the model API bill, and both are small. The thing to
actually watch is leaving the instance running after you finish.

**Stop it when idle** (you keep the disk, you stop paying for the CPU):

```bash
gcloud compute instances stop mw-audit --zone=$ZONE
```

```bash
gcloud compute instances start mw-audit --zone=$ZONE
```

**Delete it when done** (this destroys the disk, so copy `data/runs.jsonl` and
`runs/` off first):

```bash
gcloud compute instances delete mw-audit --zone=$ZONE
```

### Spot instances

`--provisioning-model=SPOT` cuts the hourly rate by roughly 60–90%, and
`run_matrix.py` is resumable at `(arm, task, trial)` granularity, so a
preemption costs at most one task run. The catch is that a preempted instance
stops mid-run and needs restarting by hand, which for a 27-hour job means
babysitting. Worth it for the full 201-task run, probably not for the pilot.

## Getting results off the box

```bash
gcloud compute scp --recurse mw-audit:~/mobileworld-stability/data ./data --zone=$ZONE
```

`runs/` is much larger — one PNG per agent step, plus an annotated copy for
clicks and drags. Pull `data/runs.jsonl` first; fetch trajectories only for the
runs you actually want to inspect.
