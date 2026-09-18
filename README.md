# OCI Ampere A1 Node Provisioner

An automated, lightweight Python and GitHub Actions engine designed to continuously request and provision an
**Oracle Cloud Infrastructure (OCI) Always Free Ampere A1 Compute Instance** (`VM.Standard.A1.Flex` with 4 vCPUs and 24 GB RAM)
across multiple Availability Domains.

OCI's Always Free A1 capacity is frequently exhausted ("Out of host capacity"). This bot retries on a
schedule via GitHub Actions until an instance successfully launches, then stops.

---

## Default Image Details

This repository is configured to deploy **Ubuntu 24.04 LTS (aarch64)**.

- **Region:** `us-phoenix-1` (Phoenix) — you can change your region to whichever you like
- **Architecture:** ARM64 (`aarch64`)
- **OS:** Canonical Ubuntu Server 24.04 LTS
- **Default Image OCID:**
  ```text
  ocid1.image.oc1.phx.aaaaaaaagrsiqy75p2vblxtrqn7ttjyafrzronnu7sfaibu6pfz6y2beeb2q
  ```
  If you deploy to a different region, this OCID is invalid — get the correct one for your region and
  shape from **Compute > Images** in the OCI Console.

---

## How it works

- `.github/workflows/oci_spawn.yml` runs `Ampere 24 ram 4 cpu/bot.py` every 5 minutes (and on manual dispatch).
- The `concurrency` block in that workflow ensures only **one** run executes at a time — new triggers queue
  instead of overlapping. This is intentional: without it, a slow run could still be in progress when the
  next cron trigger fires, risking duplicate instance launches.
- `bot.py` first checks whether an instance named `FX-Backend-Server` already exists and is active; if so it
  exits immediately (idempotent — safe to leave the schedule running forever).
- Otherwise it cycles through `PHX-AD-1`, `PHX-AD-2`, `PHX-AD-3`, retrying a small number of times per run
  (default 4 attempts, 60s apart) so each run finishes comfortably before the next is due.
- On success it exits `0`. On repeated "out of capacity" it also exits `0` (this is expected, not a failure)
  — the next scheduled run will simply try again.

---

## Setup

### 1. Prerequisites in your OCI tenancy

- An OCI account/tenancy with Always Free A1 quota available in your chosen region.
- A **VCN + public subnet** already created (note its OCID).
- An SSH key pair you control (used to log into the instance once it's up).
- An OCI **API signing key** for a user with permission to launch compute instances
  (Console → Profile → API Keys → Add API Key). Note the fingerprint and download the private key.

### 2. Required GitHub Secrets

Go to your fork → **Settings → Secrets and variables → Actions** and add:

| Secret               | Description                                              |
|-----------------------|-----------------------------------------------------------|
| `OCI_USER_ID`         | OCID of the OCI user (`ocid1.user.oc1..xxx`)              |
| `OCI_PRIVATE_KEY`     | Full contents of the API signing private key (PEM)        |
| `OCI_FINGERPRINT`     | Fingerprint of that API key                                |
| `OCI_TENANCY_ID`      | OCID of your tenancy                                       |
| `OCI_REGION`          | e.g. `us-phoenix-1`                                        |
| `OCI_SUBNET_ID`       | OCID of the subnet to attach the instance to                |
| `OCI_IMAGE_ID`        | OCID of the image (see above; must match your region)       |
| `OCI_PUBLIC_SSH_KEY`  | Your SSH **public** key, e.g. `ssh-ed25519 AAAA...`          |

See `.env.example` for the equivalent local-testing format (copy to `.env`, never commit it).

### 3. Enable the workflow

Push to your fork's `main` branch (or run it manually from the **Actions** tab via `workflow_dispatch`).
GitHub Actions schedules can lag under load — a "every 5 minutes" cron is a best-effort floor, not a guarantee.

### 4. After the instance launches

SSH in with your private key:

```bash
ssh -i /path/to/private_key ubuntu@<public_ip>
```

Get the public IP from the OCI Console (**Compute → Instances**) or via the CLI/SDK.

---

## Safety notes

- The bot is idempotent per `display_name` — running the schedule indefinitely will not create duplicate
  instances as long as the `concurrency` block in the workflow stays in place.
- If you fork this and change the schedule interval, adjust `OCI_MAX_ATTEMPTS` /
  `OCI_RETRY_DELAY_SECONDS` (env vars, see `.env.example`) so one run finishes before the next is due.
- Always Free A1 quota is capped at 4 OCPU / 24 GB **total** per tenancy — this bot requests the full
  allotment in a single instance, so don't run a second copy of this bot against the same tenancy.
