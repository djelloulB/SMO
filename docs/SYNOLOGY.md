# Deploying SMO on Synology (DSM 7.2+)

This guide runs SMO as a container on a Synology NAS with **Container Manager**.
It uses the base `docker-compose.yml`, not the Podman override file.

Two ways to reach the server from outside the LAN:

- **Scenario A**: a domain name, a Let's Encrypt certificate and the DSM reverse proxy.
- **Scenario B**: Tailscale, no domain needed.

Use only one of them for the public address. The MCP endpoint accepts only the
host in `PUBLIC_BASE_URL` (plus localhost), so that value must match the address
agents and OAuth callbacks use.

## 1. Prerequisites

- DSM 7.2 or later, with **Container Manager** installed from Package Center.
- At least 1 GB of free RAM for the container (the compose file limits it to 1 GB).
- An administrator account on the NAS and, optionally, SSH access.
- For OAuth (LinkedIn, later Meta/TikTok/Google): a public HTTPS address (scenario A or B).

## 2. Put the sources on the NAS

Create the folder `/volume1/docker/smo` (File Station), then either:

- upload the repository (File Station or SMB), **or**
- over SSH: `git clone <repo-url> /volume1/docker/smo`

Later updates: `git pull` in that folder, then rebuild the project (step 6).

## 3. Create the data folder and fix its ownership

The container runs as user `smo` (UID **1000**). The database and the dry-run
payloads are written to `./data`, so that folder must be writable by UID 1000:

```bash
ssh <admin>@<nas-ip>
cd /volume1/docker/smo
mkdir -p data
sudo chown -R 1000:1000 data
```

Without this step the container starts but fails with `unable to open database file`.

## 4. Create the `.env` file

Copy the template and edit it. Keep this file out of Git and make it readable by
the owner only:

```bash
cp .env.example .env
chmod 600 .env
```

Values to set:

| Variable | Value |
|---|---|
| `PUBLIC_BASE_URL` | `https://smo.example.com` (scenario A) or `https://smo.<tailnet>.ts.net` (scenario B) |
| `SMO_MASTER_KEY` | base64 of 32 random bytes, generated once and kept safe (see below) |
| `DRY_RUN` | `true` until you have checked a payload in `data/dryrun/` |
| `HUMAN_GATE` | `true` (default) |
| `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET` | from the LinkedIn developer app |
| `LINKEDIN_REDIRECT_URI` | `<PUBLIC_BASE_URL>/oauth/linkedin/callback` |
| `MCP_API_KEY_HERMES`, `MCP_API_KEY_ADMIN` | one random key per agent (see [MCP.md](MCP.md)) |
| `ACCOUNTS_WORDPRESS_SITE`, `WORDPRESS_APP_USERNAME`, `WORDPRESS_APP_PASSWORD` | only if you publish to WordPress |

Generate the master key and the agent keys on any machine with Python:

```bash
python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Losing `SMO_MASTER_KEY` makes the stored OAuth tokens unreadable. Keep a copy
outside the NAS.

## 5. Create the project in Container Manager

1. Open **Container Manager → Project → Create**.
2. Name: `smo`. Path: `/volume1/docker/smo`. Source: *Use existing docker-compose.yml*.
3. Select `docker-compose.yml`, then **Next**. Container Manager builds the image
   and starts the container. The first build downloads Node and Python base
   images and installs the dependencies; it can take 10 minutes on a slow NAS.
4. If Container Manager rejects the `cpus: "1.0"` line, delete that line from the
   project file and rebuild. The memory limit (`mem_limit: 1g`) is enough for
   the base profile. The limit has not been measured on a NAS yet.

## 6. Verify

From a computer on the LAN:

```
http://<nas-ip>:8023/healthz
```

Expected answer:

```json
{"status":"ok","db":"up","accounts":0,"dry_run":true,"human_gate":true}
```

The dashboard is at `http://<nas-ip>:8023/`. Check the container log in
Container Manager if the status stays `unhealthy`.

Do not expose port 8023 to the internet. Only the reverse proxy (scenario A) or
Tailscale (scenario B) should reach it.

## 7. Scenario A: reverse proxy with a domain

1. Point a DNS record (`smo.example.com`) to your public IP, and forward port 443
   on your router to the NAS. Forward nothing else.
2. **Control Panel → Login Portal → Advanced → Reverse Proxy → Create**:
   - Source: protocol `HTTPS`, hostname `smo.example.com`, port `443`.
   - Destination: protocol `HTTP`, hostname `localhost`, port `8023`.
3. **Control Panel → Security → Certificate → Add**: get a Let's Encrypt
   certificate for `smo.example.com`, then assign it to the service in *Configure*.
4. In `.env`, set `PUBLIC_BASE_URL=https://smo.example.com`, then restart the project.
5. Check from outside your network: `https://smo.example.com/healthz`.

SMO does not read `X-Forwarded-*` headers yet. The MCP endpoint checks the `Host`
header, so the proxy must forward the original host name. If `/mcp` answers `421`
through the proxy, add a custom header `Host: smo.example.com` in the proxy rule
(*Custom Header → Create → Request header*).

## 8. Scenario B: Tailscale without a domain

1. Install **Tailscale** from Package Center and sign in (`tailscale up`). If your
   DSM model does not offer the package, run Tailscale as a container next to SMO.
2. Enable MagicDNS and HTTPS certificates in the Tailscale admin console. The NAS
   gets a name such as `smo.<tailnet>.ts.net`.
3. For devices in your tailnet only:
   ```bash
   sudo tailscale serve --bg http://127.0.0.1:8023
   sudo tailscale serve status
   ```
4. For OAuth callbacks that must come from the internet, make only this port public:
   ```bash
   sudo tailscale funnel --bg 443
   ```
   Restrict the funnel in the Tailscale ACL to the callback path and `/healthz`.
5. In `.env`, set `PUBLIC_BASE_URL=https://smo.<tailnet>.ts.net`, then restart.

Check the exact `tailscale serve` syntax for your installed version with
`tailscale serve --help`.

## 9. Operations

- **Logs**: Container Manager → Container → `smo` → Log.
- **Restart after a `.env` change**: Container Manager → Project → `smo` → *Restart*.
- **Update**: `git pull` in `/volume1/docker/smo`, then Project → `smo` → *Build*
  (or *Rebuild*). Scheduled posts are kept in the database.
- **Backup**: stop the project, then copy `/volume1/docker/smo/data` and `.env` to
  a safe place. The database is `data/smoomo.db`.
- **Publishing needs the container running**: queued posts are sent by the
  scheduler inside the web server. If the NAS is off or the container is stopped,
  posts wait until it starts again.

## 10. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `unable to open database file` in the log | `data/` not writable by UID 1000. See step 3 |
| `/mcp` answers `421 Misdirected Request` | The `Host` seen by SMO is not `PUBLIC_BASE_URL`. Fix the value, or add the `Host` custom header in the reverse proxy (scenario A) |
| `/healthz` unreachable from outside | Expected: only the proxy or Tailscale is public. Test from the LAN first |
| Container shows `unhealthy` | Open the log; check the `.env` and that the database folder is writable |
| `cpus` error when creating the project | Remove the `cpus` line (step 5.4) |
| LinkedIn callback fails | `LINKEDIN_REDIRECT_URI` must equal `<PUBLIC_BASE_URL>/oauth/linkedin/callback` in both places |

## 11. Security checklist

- [ ] `.env` is `chmod 600` and not in Git.
- [ ] `DRY_RUN=true` until one payload has been checked in `data/dryrun/`.
- [ ] Only ports 443 (scenario A) or the tailnet/funnel (scenario B) are reachable from outside.
- [ ] Each agent has its own key; the `ADMIN` key stays on trusted clients only.
- [ ] `SMO_MASTER_KEY` is backed up outside the NAS.
