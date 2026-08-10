# Jellyrig — your own streaming service, on autopilot

Request a movie or show in a web app. Jellyrig finds it, downloads it, files
it, and makes it streamable — no manual steps in between. One
`docker compose up`, and your household gets a private Netflix that
maintains itself.

**Who this is for:** you can use a Linux terminal, and you can run Docker.
You do not need to know anything about Usenet or media automation — this
guide explains each part as it appears.

**What you end up with:** after about an hour (10 minutes of commands,
then a one-time walk through nine setup screens), you have your own
private streaming service:

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
4. [Connect the services (one-time)](#4-connect-the-services-one-time)
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
cp .env.example .env

# 1. (recommended) create a dedicated service account
sudo useradd -r -s /usr/sbin/nologin media
id media                        # note the uid/gid → PUID/PGID in .env

# 2. edit .env: PUID/PGID, timezone, DATA_ROOT, jellystat secrets

# 3. create the directories from section 2
sudo ./setup.sh

# 4. start everything
docker compose up -d
# or, with an NVIDIA GPU (see section 8):
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

All ten services are now running. They don't know about each other yet —
that is section 4.

## 4. Connect the services (one-time)

Work through these nine screens **in order** — each step feeds the next.
Keep two things at hand: your **provider** login and your **indexer** API key.

Two conventions used below:

- Containers talk to each other by name: `http://sonarr:8989`.
  **You** browse by address: `http://192.168.x.x:8989`.
- Every Sonarr/Radarr/Prowlarr API key is in that app's
  **Settings → General**.

---

### 4.1 SABnzbd — `http://host:8080`

*The downloader. A setup wizard opens on first visit.*

**Wizard — enter your Usenet provider:**

| Field | Value |
|---|---|
| Host | your provider's server, e.g. `news.eweka.nl` |
| Port | `563` |
| SSL | **on** |
| Username / password | from your provider |

**Settings → Folders:**

| Field | Value |
|---|---|
| Temporary Download Folder | `/data/usenet/incomplete` |
| Completed Download Folder | `/data/usenet/complete` |

**Settings → Categories** — add two:

| Category | Folder |
|---|---|
| `movies` | `movies` |
| `tv` | `tv` |

> Browse SABnzbd by IP address. A hostname like `http://mybox:8080` is
> blocked until you add it under **Config → Special → `host_whitelist`**.
> This is SABnzbd protecting you from DNS-rebinding attacks, not a bug.

**Done when:** the wizard's connection test succeeds.

---

### 4.2 Prowlarr — `http://host:9696`

*The indexer hub. Add your indexer once here; Prowlarr copies it to Sonarr
and Radarr and keeps it in sync.*

1. **Indexers → Add indexer** → find yours → paste its API key (from the
   indexer's website).
2. **Settings → Apps → Add application** — once for Sonarr, once for Radarr:

| App | Prowlarr Server | Sync server | API key |
|---|---|---|---|
| Sonarr | `http://prowlarr:9696` | `http://sonarr:8989` | Sonarr's |
| Radarr | `http://prowlarr:9696` | `http://radarr:7878` | Radarr's |

Never add indexers to Sonarr or Radarr directly — always here.

**Done when:** the indexer shows a green check in Prowlarr, and appears by
itself under Settings → Indexers in both Sonarr and Radarr.

---

### 4.3 Sonarr — `http://host:8989`

*TV automation: watches your shows and fetches new episodes forever.*

- **Settings → Media Management**
  - Add Root Folder → `/data/media/tv`
  - Turn on **Rename Episodes**
- **Settings → Download Clients → Add → SABnzbd:**

| Field | Value |
|---|---|
| Host | `sabnzbd` |
| Port | `8080` |
| API key | SABnzbd → Config → General |
| Category | `tv` |

**Done when:** the download client's Test button turns green.

---

### 4.4 Radarr — `http://host:7878`

*Movie automation. Configure it exactly like Sonarr — two values differ:*

- Root folder → `/data/media/movies`
- SABnzbd category → `movies`

**Done when:** same green Test as Sonarr.

---

### 4.5 Recyclarr — quality profiles (terminal, not a web page)

*Installs the community [TRaSH Guides](https://trash-guides.info) quality
profiles, so the automation picks good releases and rejects junk. Without
this, quality settings are a research project of their own.*

```bash
docker exec -it recyclarr recyclarr config create -t web-1080p -t hd-bluray-web
```

Edit the files it created in `config/recyclarr/configs/`: fill in the URLs
(`http://sonarr:8989`, `http://radarr:7878`) and both API keys. Then:

```bash
docker exec recyclarr recyclarr sync
```

This builds a **WEB-1080p** profile in Sonarr and **HD Bluray + WEB** in
Radarr, and re-syncs them every night at 04:00.

**Done when:** `sync` finishes without errors and the profiles appear in
each app under Settings → Profiles. Set them as the default there.

---

### 4.6 Jellyfin — `http://host:8096`

*The streaming server — the app you watch with, on any device.*

1. Wizard: create your admin account.
2. Add two libraries:

| Library type | Folder |
|---|---|
| Movies | `/data/media/movies` |
| Shows | `/data/media/tv` |

3. GPU owners only: **Dashboard → Playback → Transcoding** → pick **NVENC**
   (NVIDIA) or **VAAPI/QSV** (Intel/AMD) and enable your card's codecs.

**Done when:** both libraries exist (they are empty — that's correct).

---

### 4.7 Bazarr — `http://host:6767`

*Downloads subtitles automatically for everything that gets imported.*

- **Settings → Sonarr** and **Settings → Radarr**: hosts `sonarr` / `radarr`,
  ports `8989` / `7878`, their API keys.
- **Settings → Languages**: create a profile with your language(s), set it
  as default for both series and movies.
- **Settings → Providers**: add a couple — OpenSubtitles and Embedded
  Subtitles are good starters.

**Done when:** both connection tests pass and a default language profile
is set.

---

### 4.8 Seerr — `http://host:5055`

*The request app. This is the only address your household needs to know.*

1. **Sign in with your Jellyfin account** — same username and password.
2. Setup wizard: Jellyfin hostname `jellyfin`, port `8096` (enter them as
   separate fields — a full URL is rejected). Pick the libraries to sync.
3. Add **Radarr**: hostname `radarr`, port `7878`, its API key, quality
   profile **HD Bluray + WEB**, root folder `/data/media/movies`,
   mark as default.
4. Add **Sonarr**: hostname `sonarr`, port `8989`, its API key, quality
   profile **WEB-1080p**, root folder `/data/media/tv`, mark as default.
5. **Settings → Users**: enable auto-approve for the people you trust.

**Done when:** the Discover page shows movie posters.

---

### 4.9 Jellystat — `http://host:3000`

*Watch statistics: who watched what, when. Optional but nice.*

One-time signup, then under **Settings** enter Jellyfin's URL
(`http://jellyfin:8096`) and an API key from Jellyfin's
**Dashboard → API Keys**.

**Done when:** Jellystat lists your two libraries.

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

---

*Legal note: this stack automates downloading. What you download with it is
your responsibility.*
