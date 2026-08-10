# jellystack

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
git clone <this repo> && cd jellystack
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

Do these in order — each step feeds the next.

### 1. SABnzbd — http://host:8080
- The first-run wizard asks for your Usenet provider: host, port **563**,
  **SSL on**, your provider username/password.
- Access it by IP address. If you prefer a hostname (`http://mybox:8080`),
  SABnzbd blocks it until you add the name to Config → Special →
  `host_whitelist` — this is its DNS-rebinding protection, not a bug.
- Folders: set Temporary Download Folder `/data/usenet/incomplete` and
  Completed Download Folder `/data/usenet/complete`.
- Categories: add `movies` and `tv` — folder = the category name, relative
  to completed.

### 2. Prowlarr — http://host:9696
- Indexers → Add → your indexer (API key from its site).
- Settings → Apps → add **Sonarr** (`http://sonarr:8989`) and **Radarr**
  (`http://radarr:7878`) with their API keys (each app's Settings → General).
  Prowlarr now pushes the indexer to both automatically.

### 3. Sonarr — http://host:8989
- Settings → Media Management: add root folder `/data/media/tv`, enable
  **Rename Episodes**.
- Settings → Download Clients → SABnzbd: host `sabnzbd`, port `8080`, its
  API key, category `tv`.

### 4. Radarr — http://host:7878
- Same as Sonarr with root folder `/data/media/movies` and category `movies`.

### 5. Recyclarr (TRaSH quality profiles)
```bash
docker exec -it recyclarr recyclarr config create -t web-1080p -t hd-bluray-web
# edit config/recyclarr/configs/*.yml: paste in the Sonarr/Radarr API keys
docker exec recyclarr recyclarr sync
```
This creates tuned quality profiles (`WEB-1080p`, `HD Bluray + WEB`) with
~40 custom formats each, and re-syncs them nightly at 04:00. Set them as the
default profile in Sonarr/Radarr.

### 6. Jellyfin — http://host:8096
- First-run wizard: create your admin account.
- Add libraries: Movies → `/data/media/movies`, Shows → `/data/media/tv`.
- GPU: Dashboard → Playback → Transcoding → NVENC, enable the codecs your
  card supports.

### 7. Bazarr — http://host:6767
- Settings → Sonarr / Radarr: hosts `sonarr:8989` / `radarr:7878` + API keys.
- Settings → Languages: create a profile, set it as default for both.
- Settings → Providers: add a couple (e.g. OpenSubtitles, Embedded).

### 8. Seerr — http://host:5055
- Sign in **with your Jellyfin account** → it links to Jellyfin
  (`http://jellyfin:8096`), then add Sonarr and Radarr
  (`http://sonarr:8989` / `http://radarr:7878`, pick the TRaSH profiles and
  the root folders, enable auto-approve for yourself).
- This is the app you give the household. Requests here kick off everything.

### 9. Jellystat — http://host:3000
- One-time signup, then Settings → add your Jellyfin URL + an API key
  (Jellyfin Dashboard → API Keys).

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
