# MacSweep release & deploy

Mirrors voicetype's setup: GitHub Releases for the artifact, Lightsail nginx
for the landing page, in-app updater for end-users.

## One-time setup

You only do this stuff once.

### 1. GitHub repo

The releases live at `github.com/polistician/macsweep/releases`. If the repo
doesn't exist yet, create it:

```bash
gh repo create polistician/macsweep --public --source=. --push
```

If the repo already exists, just make sure the local clone has it as origin:

```bash
git remote -v
# expect: origin https://github.com/polistician/macsweep.git
```

### 2. DNS

Add a DNS row at your nameserver (Cloudflare for polistician.ai):

```
Type   Name     Value
A      macsweep 18.153.234.151
```

(Or CNAME → polistician.ai. Either works.)

### 3. Server: nginx + landing page directory

SSH to Lightsail and set up the document root + nginx server block:

```bash
ssh -i ~/.ssh/lightsail.pem ubuntu@18.153.234.151

# Make the document root that release.sh will rsync into:
sudo mkdir -p /opt/crypto-app/apps/macsweep
sudo chown ubuntu:ubuntu /opt/crypto-app/apps/macsweep

exit
```

Then upload the nginx config from your laptop:

```bash
scp -i ~/.ssh/lightsail.pem build/nginx-macsweep.conf \
  ubuntu@18.153.234.151:/tmp/nginx-macsweep.conf

ssh -i ~/.ssh/lightsail.pem ubuntu@18.153.234.151 "
  sudo mv /tmp/nginx-macsweep.conf /etc/nginx/sites-available/macsweep
  sudo ln -sf /etc/nginx/sites-available/macsweep /etc/nginx/sites-enabled/macsweep
  sudo nginx -t && sudo systemctl reload nginx
"
```

That's it for the one-time bits.

## Cutting a release

From the project root, on `main` (or whichever branch):

```bash
build/release.sh 0.2.0
```

That single command:

1. Bumps `VERSION` to `0.2.0`
2. Builds `MacSweep.app` and stamps the version into Info.plist
3. Packs `macsweep-0.2.0.tar.gz` (excludes `.venv`, `data/`, `.git`)
4. Generates `macsweep-0.2.0.tar.gz.sha256`
5. Commits the bump, tags `v0.2.0`, pushes to GitHub
6. Creates the GitHub Release with tarball + sha256 as assets
7. Rsyncs `site/` to `/opt/crypto-app/apps/macsweep/` so
   `https://macsweep.polistician.ai/` shows the new version

Flags:
- `--no-push` — local dry run; no git/GitHub/server side-effects
- `--no-deploy` — release on GitHub but skip the site rsync

## How users get the update

**First-time install:**
1. They visit `https://macsweep.polistician.ai/` and click "Download for macOS"
2. Downloads `macsweep-X.Y.Z.tar.gz` from GitHub Releases
3. `tar -xzf` → `cd macsweep-X.Y.Z` → `./install.sh` → open `MacSweep.app`

**Existing users:**
1. Open MacSweep → Settings → **Check for updates**
2. If newer version exists, button changes to "Install vX.Y.Z"
3. Click → progress bar (download → SHA256 verify → extract → atomic swap)
4. Button changes to "Restart now" → click → app relaunches on new version

The updater preserves the user's `data/` directory (their index, config,
quarantine) and `.venv/` (so they don't re-install dependencies on every
update).

## Safety

- Updater verifies SHA256 BEFORE swapping. Mismatch → abort.
- Atomic swap via two `rename()` calls on the same volume.
- Old project dir kept as `<project>.bak-<timestamp>` until next launch — if
  a release is broken, the user can rename it back manually.
- Updater never touches files outside the project dir.
- nginx config matches voicetype's (X-Frame-Options, X-Content-Type-Options,
  no third-party fonts/scripts blocked).

## Common operations

**Just deploy the site (no new release):**
```bash
rsync -avz --delete -e "ssh -i ~/.ssh/lightsail.pem" \
  site/ ubuntu@18.153.234.151:/opt/crypto-app/apps/macsweep/
```

**Test the in-app updater locally without publishing:**
```bash
build/release.sh 0.2.0-rc.1 --no-push
# This produces macsweep-0.2.0-rc.1.tar.gz locally. You can host it via
# `python3 -m http.server` and point MACSWEEP_OWNER/MACSWEEP_REPO env vars
# at a local URL, or just use --no-push to verify the build pipeline works.
```

**Roll back a release:**
```bash
gh release delete v0.2.0 --yes
git tag -d v0.2.0 && git push origin :refs/tags/v0.2.0
echo "0.1.x" > VERSION   # restore the prior good version manually
```
