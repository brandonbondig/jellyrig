# Jellyrig — the Jellyfin media stack, pre-assembled

A curated bundle of the best
open-source media tools — [Jellyfin](https://jellyfin.org),
[Seerr](https://github.com/seerr-team/seerr),
[Sonarr](https://sonarr.tv), [Radarr](https://radarr.video),
[Prowlarr](https://prowlarr.com), [SABnzbd](https://sabnzbd.org),
[Bazarr](https://www.bazarr.media),
[Recyclarr](https://recyclarr.dev) and
[Jellystat](https://github.com/CyferShepard/Jellystat) — pre-wired the way
the community recommends, so you skip days of wiki-reading.

Together they behave like one system: request a movie or show in Seerr, and
it is found, downloaded, filed, and streamable in Jellyfin with no manual
steps in between. One `docker compose up` plus a one-time setup walk, and
your household has a private streaming service that maintains itself.

All credit for the software belongs to those projects. This repo only
contributes the composition: a compose file, sane defaults, and a setup
guide that carries the lessons of a real build.

**Who this is for:** you can use a Linux terminal, and you can run Docker.
You do not need to know anything about Usenet or media automation — this
guide explains each part as it appears.

**What you end up with:** one command, 3–5 minutes, and you have your own
private streaming service — the installer wires every service together for
you:

```
Seerr (requests) ──► Sonarr / Radarr (automation) ──► Prowlarr (indexers)
                                   │                        │
                                   ▼                        ▼
                     SABnzbd (downloads over SSL)      your indexer
                                   │
                        instant import (hardlink/rename)
                                   │
                                   ▼
                     Jellyfin (streaming) ◄── Bazarr (subtitles)
                     Jellystat (watch statistics)
                     Recyclarr (quality profiles, synced nightly)
```

**In this guide:**

1. [What you need](#1-what-you-need)
2. [Plan your storage — read before installing](#2-plan-your-storage--read-before-installing)
3. [Install](#3-install)
4. [The few things you still choose yourself](#4-the-few-things-you-still-choose-yourself)
5. [Test the whole pipeline](#5-test-the-whole-pipeline)
6. [Day-to-day operation](#6-day-to-day-operation)
7. [Ports reference](#7-ports-reference)
8. [GPU transcoding](#8-gpu-transcoding)
9. [Troubleshooting](#9-troubleshooting)

## 1. What you need

- A Linux computer that stays on. An old laptop works well — it is quiet,
  low-power, and has a built-in battery backup.
- Docker with the compose plugin.
- A **Usenet provider** account (~€3–8/month). Usenet is a decades-old
  network of file servers; the provider is who you download from, over one
  encrypted SSL connection. Examples: Eweka, Newshosting.
- An **indexer** account (~$10–15/year). An indexer is a search engine for
  Usenet — the automation asks it where things are. Example: NZBgeek.

Everything in this repo is free and open source; the two accounts above are
the only running costs.

**Why Usenet and not torrents?** You only download — nothing is shared or
seeded from your machine, so no VPN is needed. Downloads arrive at full line
speed over a single SSL connection. The stack stays simple.

## 2. Plan your storage — read before installing

> **⚠ One rule matters more than everything else in this guide:
> downloads and library must live on ONE filesystem.**

The stack "moves" every finished download into your library. On one
filesystem, that move is an instant rename — a 22 GB file transfers in
0.2 seconds. Across two filesystems (say, downloads on the system disk,
library on a USB drive), every import becomes a full copy: slow, and it
doubles the disk wear.

You choose the location with `DATA_ROOT` in `.env`. The layout inside it:

```
$DATA_ROOT
├── usenet
│   ├── incomplete       # download working space
│   └── complete         # finished downloads, per category
└── media
    ├── movies           # Radarr's folder = Jellyfin's Movies library
    └── tv               # Sonarr's folder = Jellyfin's Shows library
```

Every container sees this tree at the same internal path (`/data`), so the
apps always agree about where files are.

**If `DATA_ROOT` is a separate drive:** mount it by UUID in `/etc/fstab`
with the `nofail` option, and add this guard so Docker waits for the mount:

```ini
# /etc/systemd/system/docker.service.d/require-data.conf
[Unit]
RequiresMountsFor=/data
```

Without the guard, a failed mount would start the stack against an empty
folder — and the automation would conclude your whole library is missing.

## 3. Install

```bash
git clone <this repo> && cd jellyrig
sudo ./setup.sh
```

That is the whole installation. `setup.sh` asks for your Usenet provider,
your indexer key, and the admin account you want, then does everything else:
creates the service user and directories, detects an NVIDIA GPU, starts the
containers, and connects every service to every other service through their
APIs.

It takes 3–5 minutes. When it finishes, the stack is ready to download.

**What it asks you**

| Question | What to enter |
|---|---|
| Storage root | one filesystem for downloads + library (section 2) |
| Timezone | detected automatically; press Enter to accept |
| Usenet provider | host, port `563`, username, password |
| Indexer | its name in Prowlarr's list, and your API key |
| Admin account | the username/password you will log in with |

Answers are saved to `.env` (mode 600, git-ignored) so a re-run does not ask
twice. **Re-running is safe**: anything already configured is skipped, so if
a step fails you fix the cause and run `sudo ./setup.sh` again.

**What it configures for you**

- SABnzbd: your provider over SSL, download folders, `movies` and `tv`
  categories, and API access for the other containers
- Sonarr and Radarr: root folders, renaming, SABnzbd as download client,
  and a Jellyfin refresh hook so imports appear immediately
- Prowlarr: your indexer, pushed automatically into Sonarr and Radarr
- Recyclarr: TRaSH Guides quality profiles (**WEB-1080p**,
  **HD Bluray + WEB**), re-synced nightly
- Jellyfin: admin account, Movies and Shows libraries
- Seerr: linked to Jellyfin, Sonarr and Radarr, with those quality profiles
- Bazarr: connected to Sonarr and Radarr

## 4. The few things you still choose yourself

`setup.sh` prints these at the end. They are left to you because they are
preferences, not plumbing.

| Service | What to do |
|---|---|
| **Bazarr** — `http://host:6767` | Settings → Languages: create a profile in your language and set it as default. Settings → Providers: enable a couple (OpenSubtitles, Embedded Subtitles). |
| **Jellystat** — `http://host:3000` | One-time signup, then Settings → Jellyfin URL `http://jellyfin:8096` and an API key from Jellyfin's Dashboard → API Keys. |
| **Jellyfin** — `http://host:8096` | With a GPU: Dashboard → Playback → Transcoding → enable NVENC (or VAAPI/QSV). See section 8. |
| **Prowlarr** — `http://host:9696` | Only if you skipped the indexer key: Indexers → Add indexer. |

## 5. Test the whole pipeline

Request something popular in Seerr. Then watch it flow:

1. Within seconds, it appears downloading in SABnzbd.
2. When the download finishes, it appears in Jellyfin within a minute.
3. Press play.

If step 2 takes minutes with heavy disk activity, your downloads and
library are on different filesystems — re-read section 2; this is the one
thing worth fixing immediately.

From now on, this pipeline runs by itself: new episodes of monitored shows
arrive without anyone asking.

## 6. Day-to-day operation

```bash
docker compose ps                              # status
docker compose logs -f sonarr                  # follow one service's log
docker compose pull && docker compose up -d    # update all services
```

- Everything restarts by itself after a reboot.
- **Back up your `config/` folder** — it holds every service's settings and
  database. The media itself is re-downloadable by design.
- On a laptop, make it ignore the lid: set `HandleLidSwitch=ignore` in
  `/etc/systemd/logind.conf`, then
  `sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target`.
- **Security:** the *arr apps and SABnzbd start with no login. On a trusted
  home network that can be acceptable; otherwise set a password in each
  app's Settings → General. Never port-forward any of these apps. For
  access from outside your home, use [Tailscale](https://tailscale.com) —
  not open ports.

## 7. Ports reference

| Service | Port | Role |
|---|---|---|
| Jellyfin | 8096 | streaming — share with household |
| Seerr | 5055 | requests — share with household |
| SABnzbd | 8080 | downloader |
| Sonarr | 8989 | TV automation |
| Radarr | 7878 | movie automation |
| Prowlarr | 9696 | indexer hub |
| Bazarr | 6767 | subtitles |
| Jellystat | 3000 | watch statistics |

## 8. GPU transcoding

**Transcoding** means converting video on the fly for a device that can't
play the original file. It only happens when needed — on a home network,
most playback is "direct play" (the file streams as-is, no conversion, no
GPU involved).

**No GPU?** The stack works as-is. Software transcoding handles roughly 1–2
simultaneous 1080p conversions on a modern quad-core. For a small household
that is usually enough. You can add a GPU later without changing anything
else.

**NVIDIA:**

1. Install the NVIDIA driver on the host. **If Secure Boot is enabled**,
   avoid `-dkms` driver packages — Secure Boot rejects their unsigned
   modules (`Key was rejected by service` in `dmesg`). On Ubuntu install
   the signed prebuilt ones, e.g. `linux-modules-nvidia-<version>-generic`.
2. Install `nvidia-container-toolkit`, then:
   `sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker`
3. Start the stack with the GPU overlay (section 3), and enable NVENC in
   Jellyfin (step 4.6).

Even a modest card (GTX 1650) handles 10+ simultaneous 1080p transcodes.

**Intel/AMD:** skip the overlay; instead add `devices: [/dev/dri:/dev/dri]`
to the `jellyfin` service and pick VAAPI or QSV in Jellyfin.

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Imports take minutes; disks thrash | downloads and library on different filesystems | put both under one `DATA_ROOT` (section 2) |
| SABnzbd says "Access denied — hostname verification failed" | browsing it by hostname | use the IP, or add the name to Config → Special → `host_whitelist` |
| Sonarr/Radarr "path does not exist" | container path mismatch | paths inside apps must start with `/data/...`, exactly as in section 2 |
| A show's episodes fail over and over | those posts were taken down | add a second indexer (different indexers carry different posts); consider allowing x265 releases for hard-to-find TV |
| NVENC missing in Jellyfin's menu | toolkit not installed, or Secure Boot rejected the driver | section 8, step 1–2; check `nvidia-smi` works on the host first |
| Whole library shows as missing after reboot | `/data` drive didn't mount before Docker started | add the `RequiresMountsFor` guard (section 2) |
| Seerr rejects the Jellyfin address | full URL pasted into the hostname field | enter hostname `jellyfin` and port `8096` as separate fields |
| `setup.sh` stops: "these ports are already in use" | another program owns a port the stack needs | stop it, or change the left-hand number in `ports:` in docker-compose.yml, then re-run |
| A wiring step failed | usually a wrong key or an unreachable service | fix the cause and run `sudo ./setup.sh` again — finished steps are skipped |

---

*Legal note: this stack automates downloading. What you download with it is
your responsibility.*
