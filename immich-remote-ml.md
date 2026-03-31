# Immich Remote ML Setup

## Architecture

```
Synology NAS (Janas)                    Mac Mini (mac-mini-2)
├── immich_server (port 2283)  ──────>  immich_machine_learning (port 3003)
├── immich_postgres                     autoheal (monitors ML health)
├── immich_redis
│
├── Tailscale IP: 100.73.219.47         Tailscale IP: 100.110.155.25
├── LAN IP: 192.168.68.70              LAN IP: 192.168.68.72
│
├── Compose: /volume1/docker/           Compose: ~/Documents/GitHub/
│   immich-app/docker-compose.yml          homelab-scripts/docker-compose.yml
└── .env: /volume1/docker/             └── .env: same directory
    immich-app/.env                         IMMICH_VERSION=v2.6.3
    IMMICH_VERSION=v2.6.3
```

The ML URL (`http://100.110.155.25:3003`) is configured in the **Immich admin web UI** at Administration > Settings > Machine Learning, not in any config file. It's stored in the Immich database.

## What broke and how it was fixed (Mar 29, 2026)

### Problem 1: ML container hung on Mac Mini

The gunicorn process inside the ML container was alive but not accepting HTTP connections. Port 3003 was open at the socket level but requests timed out. Docker saw the container as "running" and never restarted it because there was no healthcheck defined.

**Root cause**: Gunicorn worker deadlock after extended runtime (~42 hours). The process was alive (low CPU, low memory) but the uvicorn worker stopped processing requests.

**Fix**: `docker compose down && docker compose up -d` (fresh restart). Also cleared the model cache volume (`-v` flag) since models are re-downloaded on startup.

**Prevention**: Added a healthcheck and autoheal sidecar to the compose file. The healthcheck runs `/usr/src/healthcheck.py` (built into the Immich ML image) every 60s. After 3 consecutive failures, autoheal restarts the container automatically.

### Problem 2: Tailscale TUN mode lost on NAS

TCP traffic to Tailscale IPs was routed via the LAN gateway (`eth1`) instead of the `tailscale0` interface. ICMP via `tailscale ping` worked (uses WireGuard), but HTTP connections timed out.

**Root cause**: The Synology NAS reverted to userspace networking after a Tailscale service restart or reboot. The `tailscale configure-host` command grants `CAP_NET_ADMIN` and creates `/dev/net/tun`, but this doesn't persist automatically.

**Fix**: Re-ran `sudo /var/packages/Tailscale/target/bin/tailscale configure-host` then restarted the Tailscale package via DSM Package Center.

**Prevention**: A DSM scheduled task (Control Panel > Task Scheduler) runs on boot:
```bash
sleep 30
/var/packages/Tailscale/target/bin/tailscale configure-host
synopkgctl stop Tailscale
synopkgctl start Tailscale
```
Note: `synosystemctl` does NOT exist on this NAS. Use `synopkgctl` instead.

## Diagnostic playbook for agents

When Immich ML features (smart search, facial recognition, OCR) stop working, follow this sequence:

### 1. Check ML container status on Mac Mini

```bash
docker ps --format '{{.Names}}\t{{.Status}}' | grep immich
docker stats immich_machine_learning --no-stream
curl -s http://localhost:3003/ping
```

- If container is stopped: `docker compose up -d`
- If container is running but `/ping` times out: the gunicorn worker is hung. Restart with `docker compose down && docker compose up -d`
- If container is running and `/ping` returns `pong`: ML is healthy, problem is network

### 2. Check NAS → Mac Mini connectivity

```bash
# On NAS (docker/tailscale not in default PATH):
export PATH=/volume1/@appstore/ContainerManager/usr/bin:/var/packages/Tailscale/target/bin:$PATH

# Check route to Tailscale IP
ip route get 100.110.155.25
# GOOD: "dev tailscale0 table 52 src 100.73.219.47"
# BAD:  "via 192.168.68.1 dev eth1" (routing via LAN gateway)

# Test HTTP
python3 -c "import urllib.request; r=urllib.request.urlopen('http://100.110.155.25:3003/ping', timeout=10); print(r.status, r.read())"
```

- If route goes through `eth1` instead of `tailscale0`: Tailscale TUN mode is broken. Run:
  ```bash
  sudo /var/packages/Tailscale/target/bin/tailscale configure-host
  # Then restart Tailscale via DSM Package Center (Stop → Start)
  # Or: sudo synopkgctl stop Tailscale && sudo synopkgctl start Tailscale
  ```
- If route is correct but HTTP times out: check Mac Mini firewall or Tailscale ACLs

### 3. Check Immich server logs

```bash
# On NAS:
export PATH=/volume1/@appstore/ContainerManager/usr/bin:$PATH
docker logs immich_server --tail 50 2>&1 | grep -i 'machine.learning'
```

- `Machine learning server became healthy`: working
- `Machine learning request to ... failed: fetch failed`: server can't reach ML

### 4. Check the ML URL in Immich admin UI

Navigate to `http://100.73.219.47:2283/admin/system-settings` > Machine Learning > URL. Should be `http://100.110.155.25:3003`.

## Key gotchas

- **NAS PATH**: `docker` and `tailscale` are NOT in the default SSH PATH. Always prepend: `export PATH=/volume1/@appstore/ContainerManager/usr/bin:/var/packages/Tailscale/target/bin:$PATH`
- **sudo on NAS**: SSH user `janas` requires a password for sudo. Interactive terminal (`ssh -t`) is needed.
- **No curl/wget on NAS**: Use `python3` with `urllib.request` for HTTP testing.
- **Immich versions must match**: NAS server and Mac Mini ML should run the same version. Both pinned to v2.6.3 in their respective `.env` files.
- **Model loading takes time**: After a fresh start (especially with empty cache), the ML container takes 1-2 minutes to download models before responding. The healthcheck has a 120s `start_period` to account for this.
- **Autoheal**: The `autoheal` sidecar container monitors healthchecks every 120s and restarts unhealthy containers. It needs Docker socket access (`/var/run/docker.sock`).
