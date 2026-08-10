#!/usr/bin/env python3
"""Jellyrig wiring: connects every running service through its API.

Called by setup.sh after `docker compose up`. Idempotent - each step checks
before it creates, so re-running repairs a partial setup instead of
duplicating things. Standard library only.
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

# ---------------------------------------------------------------- helpers

GREEN, YELLOW, RED, DIM, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"
FAILURES: list[str] = []
NOTES: list[str] = []


def ok(msg: str) -> None:
    print(f"  {GREEN}+{RESET} {msg}")


def skip(msg: str) -> None:
    print(f"  {DIM}={RESET} {msg} {DIM}(already done){RESET}")


def fail(svc: str, msg: str) -> None:
    print(f"  {RED}x{RESET} {msg}")
    FAILURES.append(f"{svc}: {msg}")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def load_dotenv() -> None:
    path = Path(__file__).resolve().parent.parent / ".env"
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k, v)


def http(
    url: str,
    method: str = "GET",
    body: object = None,
    headers: dict | None = None,
    form: dict | None = None,
    timeout: int = 30,
):
    """Returns (status, parsed-json-or-text, response-headers)."""
    data = None
    hdrs = dict(headers or {})
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif body is not None:
        data = json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw) if raw else None, dict(r.headers)
            except ValueError:
                return r.status, raw.decode(errors="replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw) if raw else None, dict(e.headers)
        except ValueError:
            return e.code, raw.decode(errors="replace"), dict(e.headers)


def wait_for(name: str, probe, attempts: int = 60, delay: float = 2.0):
    """Poll `probe` (returns truthy when ready) with a spinner-ish message."""
    for i in range(attempts):
        try:
            result = probe()
            if result:
                return result
        except Exception:
            pass
        if i == 0:
            print(f"  {DIM}waiting for {name}...{RESET}")
        time.sleep(delay)
    raise TimeoutError(f"{name} did not come up in {int(attempts * delay)}s")


_PORTS: dict[str, str] = {}


def svc(service: str, container_port: int, scheme: str = "http") -> str:
    """Where a service is reachable from the host.

    Asks Docker for the published port instead of assuming the default, so
    the wiring still works if someone remaps ports in docker-compose.yml.
    """
    if service not in _PORTS:
        try:
            out = subprocess.run(
                ["docker", "compose", "port", service, str(container_port)],
                capture_output=True, text=True, timeout=30, cwd=REPO,
            ).stdout.strip()
            host, _, port = out.rpartition(":")
            _PORTS[service] = port if port.isdigit() else str(container_port)
        except Exception:
            _PORTS[service] = str(container_port)
    return f"{scheme}://127.0.0.1:{_PORTS[service]}"


REPO = Path(__file__).resolve().parent.parent


def config_root() -> Path:
    root = Path(env("CONFIG_ROOT", "./config"))
    if not root.is_absolute():
        root = REPO / root
    return root


def arr_api_key(app: str) -> str:
    """Sonarr/Radarr/Prowlarr write their API key to config.xml on first start."""
    path = config_root() / app / "config.xml"

    def probe():
        if path.exists():
            m = re.search(r"<ApiKey>([0-9a-f]+)</ApiKey>", path.read_text())
            if m:
                return m.group(1)
        return None

    return wait_for(f"{app} config.xml", probe)


# ---------------------------------------------------------------- sabnzbd




def sab_ini() -> Path:
    return config_root() / "sabnzbd" / "sabnzbd.ini"


def sab_key() -> str:
    def probe():
        if sab_ini().exists():
            m = re.search(r"^api_key\s*=\s*(\S+)", sab_ini().read_text(), re.M)
            if m:
                return m.group(1)
        return None

    return wait_for("sabnzbd.ini", probe)


def sab_open_access() -> None:
    """Let the other containers and this script reach SABnzbd's API.

    A fresh SABnzbd only trusts its own container hostname, and judges
    "local" by source IP - but Docker's gateway address varies by platform
    and is often outside the private ranges, so it answers 403 to everything,
    including the API we need for setup and that Sonarr/Radarr need forever.

    inet_exposure = 4 permits API and web access from any source; the API key
    stays the credential. These have to
    be written to the config file directly (the API that would change them is
    the API being blocked), so SABnzbd is stopped first to stop it writing
    the file back over us.
    """
    ini = sab_ini()
    text = ini.read_text()
    names = ["sabnzbd", "localhost", os.uname().nodename,
             os.uname().nodename.split(".")[0]]
    current = re.search(r"^host_whitelist\s*=\s*(.*)$", text, re.M)
    existing = [h for h in (current.group(1).split(",") if current else []) if h.strip()]
    whitelist = ",".join(dict.fromkeys([h.strip() for h in existing] + names))
    # private ranges only - the Docker bridge lives in 172.16/12
    ranges = "127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"

    if (current and current.group(1).strip() == whitelist
            and re.search(r"^inet_exposure\s*=\s*4", text, re.M)):
        return  # already opened up on a previous run

    subprocess.run(["docker", "compose", "stop", "sabnzbd"],
                   capture_output=True, cwd=REPO, timeout=120)
    text = ini.read_text()
    for key, value in (("host_whitelist", whitelist), ("local_ranges", ranges),
                       ("inet_exposure", "4")):
        if re.search(rf"^{key}\s*=", text, re.M):
            text = re.sub(rf"^{key}\s*=.*$", f"{key} = {value}", text, flags=re.M)
        else:
            text = text.replace("[misc]", f"[misc]\n{key} = {value}", 1)
    ini.write_text(text)
    subprocess.run(["docker", "compose", "start", "sabnzbd"],
                   capture_output=True, cwd=REPO, timeout=180)
    ok("API access opened for the other containers")


def sab_api(key: str, params: dict):
    qs = urllib.parse.urlencode({"apikey": key, "output": "json", **params})
    status, data, _ = http(f"{svc('sabnzbd', 8080)}/api?{qs}")
    return status, data


def setup_sabnzbd() -> str:
    print("\nSABnzbd")
    key = sab_key()
    sab_open_access()
    wait_for("sabnzbd api", lambda: sab_api(key, {"mode": "version"})[0] == 200)

    host = env("USENET_HOST")
    if host:
        _, cfg = sab_api(key, {"mode": "get_config", "section": "servers"})
        existing = [s.get("host") for s in (cfg or {}).get("config", {}).get("servers", [])]
        if host in existing:
            skip(f"provider {host}")
        else:
            status, data = sab_api(key, {
                "mode": "set_config", "section": "servers",
                "name": host, "displayname": host, "host": host,
                "port": env("USENET_PORT", "563"), "ssl": 1,
                "username": env("USENET_USER"), "password": env("USENET_PASS"),
                "connections": 8, "enable": 1, "priority": 0,
            })
            if status == 200 and isinstance(data, dict) and data.get("config"):
                ok(f"provider {host} (SSL :{env('USENET_PORT', '563')})")
            else:
                fail("sabnzbd", f"could not add provider: {data}")
    else:
        NOTES.append("SABnzbd: no Usenet provider entered - add one under Settings > Servers")

    for keyword, value in (("download_dir", "/data/usenet/incomplete"),
                           ("complete_dir", "/data/usenet/complete")):
        status, _ = sab_api(key, {"mode": "set_config", "section": "misc",
                                  "keyword": keyword, "value": value})
        (ok if status == 200 else lambda m: fail("sabnzbd", m))(f"{keyword} = {value}")

    _, cats = sab_api(key, {"mode": "get_config", "section": "categories"})
    have = {c["name"] for c in (cats or {}).get("config", {}).get("categories", [])}
    for cat in ("movies", "tv"):
        if cat in have:
            skip(f"category {cat}")
        else:
            sab_api(key, {"mode": "set_config", "section": "categories",
                          "name": cat, "dir": cat, "pp": 3, "priority": 0})
            ok(f"category {cat}")
    return key


# ---------------------------------------------------------------- arrs

def arr(base: str, key: str, path: str, method: str = "GET", body: object = None):
    return http(f"{base}{path}", method=method, body=body, headers={"X-Api-Key": key})


def wait_arr(name: str, base: str, key: str, api: str = "v3") -> None:
    wait_for(name, lambda: arr(base, key, f"/api/{api}/system/status")[0] == 200)


def setup_arr(name: str, base: str, key: str, root_folder: str, category: str,
              sab_apikey: str, rename_field: str) -> None:
    print(f"\n{name}")
    wait_arr(name, base, key)

    _, folders, _ = arr(base, key, "/api/v3/rootfolder")
    if any(f.get("path") == root_folder for f in folders or []):
        skip(f"root folder {root_folder}")
    else:
        status, data, _ = arr(base, key, "/api/v3/rootfolder", "POST", {"path": root_folder})
        (ok if status in (200, 201) else lambda m: fail(name, m))(f"root folder {root_folder}")

    status, naming, _ = arr(base, key, "/api/v3/config/naming")
    if status == 200 and not naming.get(rename_field):
        naming[rename_field] = True
        arr(base, key, f"/api/v3/config/naming/{naming['id']}", "PUT", naming)
        ok("renaming enabled")
    else:
        skip("renaming enabled")

    _, clients, _ = arr(base, key, "/api/v3/downloadclient")
    if any(c.get("implementation") == "Sabnzbd" for c in clients or []):
        skip("SABnzbd download client")
        return
    _, schemas, _ = arr(base, key, "/api/v3/downloadclient/schema")
    schema = next(s for s in schemas if s["implementation"] == "Sabnzbd")
    wanted = {"host": "sabnzbd", "port": 8080, "apiKey": sab_apikey,
              "tvCategory": category, "movieCategory": category, "useSsl": False}
    for f in schema["fields"]:
        if f["name"] in wanted:
            f["value"] = wanted[f["name"]]
    schema.update({"name": "SABnzbd", "enable": True, "protocol": "usenet", "priority": 1})
    status, data, _ = arr(base, key, "/api/v3/downloadclient", "POST", schema)
    if status in (200, 201):
        ok(f"SABnzbd download client (category {category})")
    else:
        fail(name, f"download client: {str(data)[:120]}")


# ---------------------------------------------------------------- prowlarr

def setup_prowlarr(key: str, sonarr_key: str, radarr_key: str) -> None:
    print("\nProwlarr")
    base = svc("prowlarr", 9696)
    wait_for("prowlarr", lambda: arr(base, key, "/api/v1/system/status")[0] == 200)

    # applications: push the indexer into sonarr + radarr automatically
    _, apps, _ = arr(base, key, "/api/v1/applications")
    have = {a.get("implementation") for a in apps or []}
    for impl, port, app_key in (("Sonarr", 8989, sonarr_key), ("Radarr", 7878, radarr_key)):
        if impl in have:
            skip(f"{impl} connection")
            continue
        _, schemas, _ = arr(base, key, "/api/v1/applications/schema")
        schema = next(s for s in schemas if s["implementation"] == impl)
        wanted = {"prowlarrUrl": "http://prowlarr:9696",
                  "baseUrl": f"http://{impl.lower()}:{port}", "apiKey": app_key}
        for f in schema["fields"]:
            if f["name"] in wanted:
                f["value"] = wanted[f["name"]]
        schema.update({"name": impl, "syncLevel": "fullSync"})
        status, data, _ = arr(base, key, "/api/v1/applications", "POST", schema)
        (ok if status in (200, 201) else lambda m: fail("prowlarr", m))(f"{impl} connection")

    # the indexer itself
    indexer_key = env("INDEXER_API_KEY")
    if not indexer_key:
        NOTES.append("Prowlarr: no indexer key entered - add your indexer in the Prowlarr UI")
        return
    name = env("INDEXER_NAME", "NZBgeek")
    _, existing, _ = arr(base, key, "/api/v1/indexer")
    if any(name.lower() in (i.get("name") or "").lower() for i in existing or []):
        skip(f"indexer {name}")
        return
    _, schemas, _ = arr(base, key, "/api/v1/indexer/schema")
    schema = next((s for s in schemas
                   if s.get("name", "").lower() == name.lower()
                   and s.get("protocol") == "usenet"), None)
    if not schema:
        fail("prowlarr", f"indexer '{name}' not found in Prowlarr's catalogue")
        return
    for f in schema["fields"]:
        if f["name"] == "apiKey":
            f["value"] = indexer_key
    schema.update({"enable": True, "appProfileId": 1})
    status, data, _ = arr(base, key, "/api/v1/indexer", "POST", schema)
    if status in (200, 201):
        ok(f"indexer {name} added and syncing to Sonarr/Radarr")
    else:
        fail("prowlarr", f"indexer: {str(data)[:150]}")


# ---------------------------------------------------------------- recyclarr

def setup_recyclarr(sonarr_key: str, radarr_key: str) -> None:
    print("\nRecyclarr (TRaSH quality profiles)")
    cfg_dir = config_root() / "recyclarr" / "configs"
    if not cfg_dir.exists() or not list(cfg_dir.glob("*.yml")):
        subprocess.run(
            ["docker", "exec", "recyclarr", "recyclarr", "config", "create",
             "-t", "web-1080p", "-t", "hd-bluray-web"],
            capture_output=True, text=True, timeout=120,
        )
    files = sorted(cfg_dir.glob("*.yml")) if cfg_dir.exists() else []
    if not files:
        fail("recyclarr", "config create produced no files")
        return

    # The templates ship placeholders ("Put your Sonarr URL here"). Fill them
    # in per section: a file can hold sonarr: and/or radarr: blocks, and each
    # needs its own url + key.
    creds = {"sonarr": ("http://sonarr:8989", sonarr_key),
             "radarr": ("http://radarr:7878", radarr_key)}
    for f in files:
        section = None
        out = []
        for line in f.read_text().splitlines():
            stripped = line.strip()
            if stripped in ("sonarr:", "radarr:"):
                section = stripped[:-1]
            elif section:
                indent = line[: len(line) - len(line.lstrip())]
                if stripped.startswith("base_url:"):
                    line = f"{indent}base_url: {creds[section][0]}"
                elif stripped.startswith("api_key:"):
                    line = f"{indent}api_key: {creds[section][1]}"
            out.append(line)
        f.write_text("\n".join(out) + "\n")

    r = subprocess.run(["docker", "exec", "recyclarr", "recyclarr", "sync"],
                       capture_output=True, text=True, timeout=900)
    # `sync` exits 0 even when it did nothing, so confirm against the apps
    made = []
    for app, base, key, want in (
        ("Sonarr", svc("sonarr", 8989), sonarr_key, "WEB-1080p"),
        ("Radarr", svc("radarr", 7878), radarr_key, "HD Bluray + WEB"),
    ):
        _, profiles, _ = arr(base, key, "/api/v3/qualityprofile")
        if any(p.get("name") == want for p in profiles or []):
            made.append(f"{want} ({app})")
    if len(made) == 2:
        ok("profiles created: " + ", ".join(made))
    else:
        detail = (r.stderr or r.stdout or "").strip().splitlines()
        fail("recyclarr", "profiles not created - " +
             (detail[-1][:150] if detail else "see: docker exec recyclarr recyclarr sync"))


# ---------------------------------------------------------------- jellyfin

JF_HDR = {"X-Emby-Authorization":
          'MediaBrowser Client="jellyrig-setup", Device="cli", DeviceId="jellyrig", Version="1.0"'}


def setup_jellyfin() -> tuple[str, str]:
    """Returns (user token, api key for other services)."""
    print("\nJellyfin")
    user, pw = env("ADMIN_USER"), env("ADMIN_PASS")
    wait_for("jellyfin", lambda: http(f"{svc("jellyfin", 8096)}/System/Info/Public")[0] == 200)

    status, info, _ = http(f"{svc("jellyfin", 8096)}/System/Info/Public")
    if info.get("StartupWizardCompleted"):
        skip("startup wizard")
    else:
        http(f"{svc("jellyfin", 8096)}/Startup/Configuration", "POST",
             {"UICulture": "en-US", "MetadataCountryCode": "US",
              "PreferredMetadataLanguage": "en"}, JF_HDR)
        http(f"{svc("jellyfin", 8096)}/Startup/User", "GET", headers=JF_HDR)
        http(f"{svc("jellyfin", 8096)}/Startup/User", "POST", {"Name": user, "Password": pw}, JF_HDR)
        http(f"{svc("jellyfin", 8096)}/Startup/RemoteAccess", "POST",
             {"EnableRemoteAccess": True, "EnableAutomaticPortMapping": False}, JF_HDR)
        status, _, _ = http(f"{svc("jellyfin", 8096)}/Startup/Complete", "POST", headers=JF_HDR)
        ok(f"admin account '{user}' created" if status in (200, 204) else "wizard finished")

    def try_auth():
        s, d, _ = http(f"{svc("jellyfin", 8096)}/Users/AuthenticateByName", "POST",
                       {"Username": user, "Pw": pw}, JF_HDR)
        return d["AccessToken"] if s == 200 else None

    token = wait_for("jellyfin login", try_auth, attempts=15)
    auth = {**JF_HDR, "X-Emby-Token": token}

    _, folders, _ = http(f"{svc("jellyfin", 8096)}/Library/VirtualFolders", headers=auth)
    have = {v["Name"] for v in folders or []}
    for name, ctype, path in (("Movies", "movies", "/data/media/movies"),
                              ("Shows", "tvshows", "/data/media/tv")):
        if name in have:
            skip(f"library {name}")
            continue
        qs = urllib.parse.urlencode({"name": name, "collectionType": ctype,
                                     "paths": path, "refreshLibrary": "true"})
        s, d, _ = http(f"{svc("jellyfin", 8096)}/Library/VirtualFolders?{qs}", "POST",
                       {"LibraryOptions": {"PathInfos": [{"Path": path}]}}, auth)
        (ok if s in (200, 204) else lambda m: fail("jellyfin", m))(f"library {name} -> {path}")

    # an API key for sonarr/radarr notifications + jellystat
    _, keys, _ = http(f"{svc("jellyfin", 8096)}/Auth/Keys", headers=auth)
    key = next((k["AccessToken"] for k in (keys or {}).get("Items", [])
                if k.get("AppName") == "jellyrig"), None)
    if not key:
        http(f"{svc("jellyfin", 8096)}/Auth/Keys?App=jellyrig", "POST", headers=auth)
        _, keys, _ = http(f"{svc("jellyfin", 8096)}/Auth/Keys", headers=auth)
        key = next((k["AccessToken"] for k in (keys or {}).get("Items", [])
                    if k.get("AppName") == "jellyrig"), "")
    return token, key or ""


def arr_jellyfin_notification(name: str, base: str, key: str, jf_key: str) -> None:
    if not jf_key:
        return
    _, existing, _ = arr(base, key, "/api/v3/notification")
    if any(n.get("implementation") == "MediaBrowser" for n in existing or []):
        skip(f"{name}: Jellyfin refresh hook")
        return
    _, schemas, _ = arr(base, key, "/api/v3/notification/schema")
    schema = next((s for s in schemas if s["implementation"] == "MediaBrowser"), None)
    if not schema:
        return
    wanted = {"host": "jellyfin", "port": 8096, "apiKey": jf_key, "updateLibrary": True}
    for f in schema["fields"]:
        if f["name"] in wanted:
            f["value"] = wanted[f["name"]]
    schema.update({"name": "Jellyfin", "onDownload": True, "onUpgrade": True, "onRename": True})
    s, d, _ = arr(base, key, "/api/v3/notification", "POST", schema)
    (ok if s in (200, 201) else lambda m: fail(name, m))(f"{name}: Jellyfin refresh hook")


# ---------------------------------------------------------------- seerr



def seerr_profile_id(base: str, key: str, profile_name: str) -> tuple[int, str]:
    _, profiles, _ = arr(base, key, "/api/v3/qualityprofile")
    for p in profiles or []:
        if p["name"] == profile_name:
            return p["id"], p["name"]
    first = (profiles or [{}])[0]
    return first.get("id", 1), first.get("name", "Any")


def setup_seerr(sonarr_key: str, radarr_key: str) -> None:
    print("\nSeerr")
    user, pw = env("ADMIN_USER"), env("ADMIN_PASS")
    wait_for("seerr", lambda: http(f"{svc("jellyseerr", 5055)}/api/v1/status")[0] == 200)

    _, pub, _ = http(f"{svc("jellyseerr", 5055)}/api/v1/settings/public")
    initialized = bool((pub or {}).get("initialized"))

    body = {"username": user, "password": pw}
    if not initialized:
        body.update({"hostname": "jellyfin", "port": 8096, "useSsl": False,
                     "urlBase": "", "email": f"{user}@localhost",
                     # 2 = Jellyfin. Omitting it fails with NO_ADMIN_USER.
                     "serverType": 2})
    s, d, hdrs = http(f"{svc("jellyseerr", 5055)}/api/v1/auth/jellyfin", "POST", body)
    if s != 200:
        fail("seerr", f"login: {str(d)[:150]}")
        return
    cookie = (hdrs.get("Set-Cookie") or "").split(";")[0]
    ck = {"Cookie": cookie}
    ok("linked to Jellyfin" if not initialized else "signed in")

    # enable all synced libraries (GET without enable= disables them!)
    s, libs, _ = http(f"{svc("jellyseerr", 5055)}/api/v1/settings/jellyfin/library?sync=true", headers=ck)
    ids = ",".join(x["id"] for x in libs or [])
    if ids:
        http(f"{svc("jellyseerr", 5055)}/api/v1/settings/jellyfin/library?enable={ids}", headers=ck)
        ok(f"libraries enabled ({len(libs)})")

    _, radarrs, _ = http(f"{svc("jellyseerr", 5055)}/api/v1/settings/radarr", headers=ck)
    if radarrs:
        skip("Radarr connection")
    else:
        pid, pname = seerr_profile_id(svc("radarr", 7878), radarr_key, "HD Bluray + WEB")
        s, d, _ = http(f"{svc("jellyseerr", 5055)}/api/v1/settings/radarr", "POST", {
            "name": "Radarr", "hostname": "radarr", "port": 7878, "apiKey": radarr_key,
            "useSsl": False, "baseUrl": "", "activeProfileId": pid, "activeProfileName": pname,
            "activeDirectory": "/data/media/movies", "is4k": False, "isDefault": True,
            "minimumAvailability": "released", "tags": [], "syncEnabled": True,
        }, ck)
        (ok if s in (200, 201) else lambda m: fail("seerr", m))(f"Radarr connected (profile {pname})")

    _, sonarrs, _ = http(f"{svc("jellyseerr", 5055)}/api/v1/settings/sonarr", headers=ck)
    if sonarrs:
        skip("Sonarr connection")
    else:
        pid, pname = seerr_profile_id(svc("sonarr", 8989), sonarr_key, "WEB-1080p")
        s, d, _ = http(f"{svc("jellyseerr", 5055)}/api/v1/settings/sonarr", "POST", {
            "name": "Sonarr", "hostname": "sonarr", "port": 8989, "apiKey": sonarr_key,
            "useSsl": False, "baseUrl": "", "activeProfileId": pid, "activeProfileName": pname,
            "activeDirectory": "/data/media/tv", "activeLanguageProfileId": 1,
            "activeAnimeProfileId": pid, "activeAnimeProfileName": pname,
            "activeAnimeDirectory": "/data/media/tv", "activeAnimeLanguageProfileId": 1,
            "is4k": False, "isDefault": True, "enableSeasonFolders": True, "tags": [],
            "syncEnabled": True,
        }, ck)
        (ok if s in (200, 201) else lambda m: fail("seerr", m))(f"Sonarr connected (profile {pname})")

    if not initialized:
        http(f"{svc("jellyseerr", 5055)}/api/v1/settings/initialize", "POST", headers=ck)
        ok("setup marked complete")


# ---------------------------------------------------------------- bazarr

def setup_bazarr(sonarr_key: str, radarr_key: str) -> None:
    print("\nBazarr")
    cfg = config_root() / "bazarr" / "config" / "config.yaml"

    def probe():
        if cfg.exists():
            m = re.search(r"apikey:\s*(\S+)", cfg.read_text())
            if m:
                return m.group(1).strip("'\"")
        return None

    try:
        key = wait_for("bazarr config", probe, attempts=30)
        wait_for("bazarr api", lambda: http(
            f"{svc('bazarr', 6767)}/api/system/status",
            headers={"X-API-KEY": key})[0] == 200)
        form = {
            "settings-general-use_sonarr": "true",
            "settings-sonarr-ip": "sonarr", "settings-sonarr-port": "8989",
            "settings-sonarr-base_url": "/", "settings-sonarr-ssl": "false",
            "settings-sonarr-apikey": sonarr_key,
            "settings-general-use_radarr": "true",
            "settings-radarr-ip": "radarr", "settings-radarr-port": "7878",
            "settings-radarr-base_url": "/", "settings-radarr-ssl": "false",
            "settings-radarr-apikey": radarr_key,
        }
        s, d, _ = http(f"{svc('bazarr', 6767)}/api/system/settings", "POST",
                       form=form, headers={"X-API-KEY": key})
        if s in (200, 204):
            ok("connected to Sonarr and Radarr")
            NOTES.append("Bazarr: pick your subtitle LANGUAGES and PROVIDERS in its UI "
                         "(Settings > Languages / Providers) - that choice is yours")
        else:
            fail("bazarr", f"settings: http {s}")
    except TimeoutError as e:
        fail("bazarr", str(e))


# ---------------------------------------------------------------- main

def main() -> int:
    load_dotenv()

    sab_apikey = setup_sabnzbd()

    sonarr_key = arr_api_key("sonarr")
    radarr_key = arr_api_key("radarr")
    prowlarr_key = arr_api_key("prowlarr")

    setup_arr("Sonarr", svc("sonarr", 8989), sonarr_key,
              "/data/media/tv", "tv", sab_apikey, "renameEpisodes")
    setup_arr("Radarr", svc("radarr", 7878), radarr_key,
              "/data/media/movies", "movies", sab_apikey, "renameMovies")
    setup_prowlarr(prowlarr_key, sonarr_key, radarr_key)
    setup_recyclarr(sonarr_key, radarr_key)

    try:
        _, jf_key = setup_jellyfin()
        arr_jellyfin_notification("Sonarr", svc("sonarr", 8989), sonarr_key, jf_key)
        arr_jellyfin_notification("Radarr", svc("radarr", 7878), radarr_key, jf_key)
    except Exception as e:
        fail("jellyfin", str(e)[:150])

    try:
        setup_seerr(sonarr_key, radarr_key)
    except Exception as e:
        fail("seerr", str(e)[:150])

    setup_bazarr(sonarr_key, radarr_key)
    NOTES.append("Jellystat (http://host:3000): one-time signup in its UI, then add "
                 "Jellyfin http://jellyfin:8096 + an API key (Dashboard > API Keys)")

    print(f"\n{'-' * 60}")
    if FAILURES:
        print(f"{RED}Finished with {len(FAILURES)} problem(s):{RESET}")
        for f in FAILURES:
            print(f"  x {f}")
        print("Fix the cause and re-run ./setup.sh - completed steps are skipped.")
    else:
        print(f"{GREEN}All services wired.{RESET}")
    if NOTES:
        print("\nStill yours to do:")
        for n in NOTES:
            print(f"  - {n}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
