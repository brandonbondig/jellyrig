# jellyrig

A complete, Usenet-based, fully automated home media server in one
`docker compose up`. Request a movie or show in a web UI → it is found,
downloaded, imported, and streamable — with zero manual steps in between.

```
Seerr (requests) ──► Sonarr / Radarr (automation) ──► Prowlarr (indexers)
                                   │                        │
                                   ▼                        ▼
                     SABnzbd (downloads via Usenet, SSL)  your indexer(s)
                                   │
                     instant hardlink/rename import (one filesystem!)
                                   │
                                   ▼
                     Jellyfin (streaming) ◄── Bazarr (subtitles)
                     Jellystat (watch statistics)
                     Recyclarr (TRaSH quality profiles, auto-synced nightly)
```

**Why Usenet-only, no VPN?** Downloads come over a single SSL connection to
your provider (port 563) — nothing is shared or seeded, so there is nothing a
VPN needs to hide. Fewer moving parts, full line speed.

**What it costs:** a Usenet provider subscription (~€3–8/month) and an
indexer (~$10–15/year). Everything in this repo is free.

## Requirements

- Any Linux box (an old laptop is genuinely great — built-in UPS)
- Docker + the compose plugin
- A Usenet provider account (e.g. Eweka, Newshosting — pick one on the
  Omicron or UsenetExpress backbone)
- A Usenet indexer account (e.g. NZBgeek)
- Optional: an NVIDIA GPU for hardware transcoding

## Quick start

```bash
git clone <this repo> && cd jellyrig
cp .env.example .env

# 1. (recommended) dedicated service account
sudo useradd -r -s /usr/sbin/nologin media
id media                        # note uid/gid, put them in .env as PUID/PGID

# 2. edit .env  (timezone, DATA_ROOT, jellystat secrets)

# 3. create directories
sudo ./setup.sh

# 4. up
docker compose up -d
# with NVIDIA transcoding:
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

Then walk the **first-run wiring** below — ~20 minutes, done once.

## The /data layout (read this one section)

```
$DATA_ROOT                  # ONE filesystem - this is non-negotiable
├── usenet
│   ├── incomplete          # SABnzbd working space
│   └── complete            # SABnzbd finished downloads (per category)
├── media
│   ├── movies              # Radarr root folder = Jellyfin Movies library
│   └── tv                  # Sonarr root folder  = Jellyfin Shows library
```

Downloads and library **must live on the same filesystem**. Then an import is
an instant `rename()` — a 22 GB movie "moves" in 0.2 seconds. Split them
across drives and every import becomes a full copy that hammers both disks.
All containers see this tree at the same path (`/data`), which is what makes
the *arr path handling just work.

If `$DATA_ROOT` is a separate drive, mount it by UUID in `/etc/fstab` with
`nofail`, and add a guard so Docker never starts against an empty mountpoint
(which would make the *arrs think the whole library vanished):

```ini
# /etc/systemd/system/docker.service.d/require-data.conf
[Unit]
RequiresMountsFor=/data
```

## First-run wiring

One pass, in order — each step feeds the next. Have two things ready:
your **Usenet provider** login and your **indexer** API key.

Throughout: containers talk to each other by name (`http://sonarr:8989`),
while **you** browse by host IP (`http://192.168.x.x:8989`). Every *arr app's
API key lives in its **Settings → General**.

---

### 1 · SABnzbd — `http://host:8080`

*The downloader. A first-run wizard opens on first visit.*

**Wizard — your Usenet provider:**

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

> Browse it by IP. A hostname like `http://mybox:8080` is blocked until you
> add it to **Config → Special → `host_whitelist`** — that's SABnzbd's
> DNS-rebinding protection, not a bug.

---

### 2 · Prowlarr — `http://host:9696`

*One search hub: add your indexer once, Prowlarr pushes it everywhere.*

1. **Indexers → Add indexer** → find yours → paste its API key (from the
   indexer's website).
2. **Settings → Apps → Add application**, twice:

| App | Prowlarr Server | Sync server | API key |
|---|---|---|---|
| Sonarr | `http://prowlarr:9696` | `http://sonarr:8989` | Sonarr's |
| Radarr | `http://prowlarr:9696` | `http://radarr:7878` | Radarr's |

The indexer now appears in Sonarr and Radarr automatically — never add
indexers to them by hand.

---

### 3 · Sonarr — `http://host:8989`

*TV automation: monitors shows, grabs new episodes forever.*

- **Settings → Media Management**
  - Add Root Folder → `/data/media/tv`
  - Enable **Rename Episodes**
- **Settings → Download Clients → Add → SABnzbd**

| Field | Value |
|---|---|
| Host | `sabnzbd` |
| Port | `8080` |
| API key | SABnzbd → Config → General |
| Category | `tv` |

---

### 4 · Radarr — `http://host:7878`

*Movie automation. Identical to Sonarr, two values differ:*

- Root folder → `/data/media/movies`
- SABnzbd category → `movies`

---

### 5 · Recyclarr — quality profiles, no UI

*Applies the community [TRaSH Guides](https://trash-guides.info) so releases
are scored sanely (proper web/bluray tiers, junk releases rejected).*

```bash
docker exec -it recyclarr recyclarr config create -t web-1080p -t hd-bluray-web
```

Edit the files it created under `config/recyclarr/configs/` — fill in the
Sonarr and Radarr URLs (`http://sonarr:8989`, `http://radarr:7878`) and API
keys — then:

```bash
docker exec recyclarr recyclarr sync
```

This builds a **WEB-1080p** profile in Sonarr and **HD Bluray + WEB** in
Radarr (~40 custom formats each) and re-syncs nightly at 04:00. Set each as
the default profile in its app.

---

### 6 · Jellyfin — `http://host:8096`

*The streaming server — this is what you watch with.*

1. Wizard: create your admin account.
2. Add libraries:

| Library type | Folder |
|---|---|
| Movies | `/data/media/movies` |
| Shows | `/data/media/tv` |

3. GPU only: **Dashboard → Playback → Transcoding** → select **NVENC**
   (or VAAPI/QSV for Intel/AMD) and enable the codecs your card supports.

---

### 7 · Bazarr — `http://host:6767`

*Fetches subtitles for everything the *arrs import.*

- **Settings → Sonarr** and **Settings → Radarr**: hosts `sonarr` / `radarr`,
  ports `8989` / `7878`, their API keys.
- **Settings → Languages**: create a language profile, set it as default
  for both series and movies.
- **Settings → Providers**: add a couple (OpenSubtitles, Embedded
  Subtitles are good starters).

---

### 8 · Seerr — `http://host:5055`

*The request app — the only URL the household needs.*

1. **Sign in with your Jellyfin account** (same username/password).
2. Setup wizard: Jellyfin hostname `jellyfin`, port `8096` — then pick the
   libraries to sync.
3. Add **Radarr**: hostname `radarr`, port `7878`, API key, quality profile
   **HD Bluray + WEB**, root folder `/data/media/movies`, mark as default.
4. Add **Sonarr**: hostname `sonarr`, port `8989`, API key, quality profile
   **WEB-1080p**, root folder `/data/media/tv`, mark as default.
5. **Settings → Users**: give auto-approve to the people you trust.

A request here now drives the entire pipeline unattended.

---

### 9 · Jellystat — `http://host:3000`

*Watch statistics (who watched what, when).*

One-time signup, then **Settings**: Jellyfin URL `http://jellyfin:8096` + an
API key from Jellyfin's **Dashboard → API Keys**.

---

### Verify the loop
Request something in Seerr. Watch it appear in SABnzbd within seconds,
import moments after completion, and show up in Jellyfin. If import is slow
(minutes, disk thrashing), downloads and library are NOT on one filesystem —
fix that first.

## Ports

| Service | Port | Purpose |
|---|---|---|
| Jellyfin | 8096 | streaming |
| Seerr | 5055 | requests (give this to the household) |
| SABnzbd | 8080 | downloader |
| Sonarr | 8989 | TV automation |
| Radarr | 7878 | movie automation |
| Prowlarr | 9696 | indexer management |
| Bazarr | 6767 | subtitles |
| Jellystat | 3000 | watch statistics |

## GPU transcoding — and running without one

**No GPU? The stack still works out of the box.** Most home streaming
direct-plays: the file goes to the client as-is and transcoding never
happens. When a transcode IS needed (an old TV, a low-bandwidth remote
client), Jellyfin falls back to software encoding — figure on a modern
quad-core handling 1–2 simultaneous 1080p transcodes. For a small household
on a LAN that is usually plenty; add a GPU later if you outgrow it, nothing
else changes.

**NVIDIA:**

1. Install the NVIDIA driver on the host. **Secure Boot gotcha:** DKMS-built
   modules are rejected by Secure Boot (`Key was rejected by service` in
   dmesg). On Ubuntu, install the signed prebuilt modules instead, e.g.
   `linux-modules-nvidia-<version>-generic`, rather than the `-dkms` package.
2. Install `nvidia-container-toolkit` and run
   `sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker`.
3. Start with the GPU overlay (Quick start above), then enable NVENC in
   Jellyfin's transcoding settings.

Even a modest Turing-era card (GTX 1650) handles ~10+ simultaneous 1080p
transcodes; most LAN playback direct-plays and uses no GPU at all.

Intel/AMD instead: skip the overlay and add to the `jellyfin` service:
`devices: [/dev/dri:/dev/dri]`, then pick VAAPI/QSV in Jellyfin.

## Operating it

```bash
docker compose ps                              # status
docker compose logs -f sonarr                  # tail a service
docker compose pull && docker compose up -d    # update everything
```

- Everything restarts on boot (`restart: unless-stopped`).
- Using a laptop: ignore the lid switch and mask sleep so it keeps serving —
  `HandleLidSwitch=ignore` in `/etc/systemd/logind.conf`, then
  `sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target`.
- Back up `$CONFIG_ROOT` (it holds every service's database and settings);
  the media itself is re-downloadable by design.
- The *arr apps and SABnzbd ship with **no authentication**. On a trusted LAN
  that may be fine; otherwise set a password in each app's
  Settings → General, and never port-forward them. For remote access use
  [Tailscale](https://tailscale.com) rather than exposing ports.

## Notes

- Sonarr/Radarr → Settings → Connect → Jellyfin webhook is worth adding so
  the library refreshes the instant an import lands.
- Missing-episode gotcha: if a show's episodes fail repeatedly, the posts may
  have been taken down. A second indexer (different posts) and/or a
  "block account" on a different Usenet backbone as a fill server in SABnzbd
  are the standard fixes. Also consider allowing x265 releases for
  hard-to-find TV (TRaSH's default profiles reject x265 at 1080p).
- Legal: this stack automates downloading; what you download is on you.
