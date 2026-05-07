#!/usr/bin/env bash
# MacSweep release pipeline — voicetype-style.
#
# What this does, in order:
#   1. Bump the VERSION file (you pass the new version as $1).
#   2. Build the .app wrapper (build_app.sh).
#   3. Stamp the new version into the .app's Info.plist.
#   4. Pack the project into macsweep-<ver>.tar.gz (excludes .venv, data, .git).
#   5. Compute SHA256 sidecar.
#   6. Commit + tag the bump.
#   7. Push tag to GitHub.
#   8. Create a GitHub Release with the tarball + sha256 as assets.
#   9. Rsync the site/ directory to Lightsail (so macsweep.polistician.ai
#      shows the new version + download link).
#
# Prereqs (one-time):
#   - `gh` CLI installed and authenticated (gh auth login).
#   - SSH access to Lightsail at the path in $SERVER below.
#   - Server has /opt/crypto-app/apps/macsweep/ writable by the ubuntu user.
#
# Usage:
#   build/release.sh 0.2.0
#   build/release.sh 0.2.0 --no-push       # local-only dry run (no GH, no rsync)
#   build/release.sh 0.2.0 --no-deploy     # release on GH but skip site deploy

set -euo pipefail

# A scopeless GITHUB_TOKEN env var will override gh's keyring auth and break
# `gh repo create` / `gh release create` with a "scope" error. The keyring
# token (gho_…) created by `gh auth login` has the right scopes; just unset
# the env var here so it doesn't get in the way.
unset GITHUB_TOKEN

NEW_VERSION="${1:-}"
SKIP_PUSH=0
SKIP_DEPLOY=0
shift || true
for arg in "$@"; do
  case "$arg" in
    --no-push) SKIP_PUSH=1 ;;
    --no-deploy) SKIP_DEPLOY=1 ;;
  esac
done

if [ -z "$NEW_VERSION" ]; then
  echo "usage: $0 <new-version> [--no-push] [--no-deploy]"
  echo "       e.g. $0 0.2.0"
  exit 1
fi

# Sanity check version format (loose semver)
if ! [[ "$NEW_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-.].+)?$ ]]; then
  echo "✗ version must look like X.Y.Z (got: $NEW_VERSION)"
  exit 1
fi

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
SERVER="ubuntu@18.153.234.151"
SSH_KEY="$HOME/.ssh/lightsail.pem"
SERVER_SITE_DIR="/opt/crypto-app/apps/macsweep"

OLD_VERSION="$(cat VERSION 2>/dev/null || echo '0.0.0')"
echo "── MacSweep release: $OLD_VERSION → $NEW_VERSION ──"

# 1. Bump VERSION
echo "$NEW_VERSION" > VERSION

# 2. Build the .app wrapper
echo "→ Building .app wrapper…"
./build_app.sh > /tmp/macsweep-build.log 2>&1 || {
  echo "✗ build_app.sh failed. Log:"; cat /tmp/macsweep-build.log; exit 1;
}

# 3. Stamp version into Info.plist
PLIST="MacSweep.app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $NEW_VERSION" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $NEW_VERSION" "$PLIST" 2>/dev/null || true

# 4. Pack the tarball. Exclude things that should NOT ship:
#    - .venv (recreated on first launch)
#    - data/ (user's index, config, quarantine)
#    - .git, __pycache__, build artifacts
TAR="macsweep-$NEW_VERSION.tar.gz"
TARDIR="macsweep-$NEW_VERSION"
echo "→ Packing $TAR …"

STAGE="/tmp/$TARDIR"
rm -rf "$STAGE"
mkdir -p "$STAGE"
# Use rsync to copy what we want in.
rsync -a \
  --exclude='.venv' \
  --exclude='data/' \
  --exclude='.git' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.pytest_cache' \
  --exclude='node_modules' \
  --exclude='/MacSweep.app' \
  --exclude='build/macsweep-*.tar.gz*' \
  --exclude='*.log' \
  --exclude='.DS_Store' \
  ./ "$STAGE/"

# Include the freshly-built .app so the user gets it without rebuilding.
cp -R "MacSweep.app" "$STAGE/MacSweep.app"

# Add a bare install.sh so first-time users can drop the tarball anywhere.
cat > "$STAGE/install.sh" <<'INSTALL_SH'
#!/usr/bin/env bash
# MacSweep first-time install helper. Sets up the .venv and (optionally)
# moves MacSweep.app to /Applications.
set -euo pipefail
cd "$(dirname "$0")"
echo "→ Setting up Python venv…"
/usr/bin/python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt
echo "→ Done. Launch: open MacSweep.app   (or move it to /Applications first)"
INSTALL_SH
chmod +x "$STAGE/install.sh"

(cd /tmp && tar -czf "$ROOT/$TAR" "$TARDIR")
rm -rf "$STAGE"

# 5. SHA256 sidecar
echo "→ Computing SHA256…"
shasum -a 256 "$TAR" > "$TAR.sha256"
SHA="$(cut -d' ' -f1 < "$TAR.sha256")"
echo "  $SHA"

if [ "$SKIP_PUSH" = "1" ]; then
  echo "✓ Local build complete. --no-push set, skipping git/GitHub/server."
  echo "  Artifact: $TAR"
  exit 0
fi

# 6. Commit + tag
echo "→ Committing version bump…"
git add VERSION
git commit -m "release: v$NEW_VERSION" || echo "  (nothing to commit)"
git tag -a "v$NEW_VERSION" -m "v$NEW_VERSION" || true

# 7. Push
echo "→ Pushing to GitHub…"
git push origin HEAD
git push origin "v$NEW_VERSION"

# 8. GitHub Release
# Heredoc tag is quoted ('NOTES') so NOTHING gets expanded inside — no
# `set -u` traps on stray `$` references in markdown body. We sed in the
# two values we actually need (version + sha).
echo "→ Creating GitHub Release v${NEW_VERSION}…"
NOTES_FILE="$(mktemp)"
cat > "$NOTES_FILE" <<'NOTES'
## MacSweep v__VER__

### Install
1. Download `macsweep-__VER__.tar.gz` from this release.
2. `tar -xzf macsweep-__VER__.tar.gz && cd macsweep-__VER__ && ./install.sh`
3. Open `MacSweep.app` (right-click → Open the first time).

### Update an existing install
Inside MacSweep: Settings → **Check for updates** → click → restart.

### Verify integrity
```
shasum -a 256 macsweep-__VER__.tar.gz
# expect: __SHA__
```
NOTES
sed -i.bak -e "s|__VER__|${NEW_VERSION}|g" -e "s|__SHA__|${SHA}|g" "$NOTES_FILE" && rm -f "${NOTES_FILE}.bak"

gh release create "v$NEW_VERSION" \
  "$TAR" \
  "$TAR.sha256" \
  --title "v$NEW_VERSION" \
  --notes-file "$NOTES_FILE"
rm -f "$NOTES_FILE"

# 9. Site deploy
if [ "$SKIP_DEPLOY" = "1" ]; then
  echo "✓ Release published. --no-deploy set, skipping site rsync."
else
  echo "→ Deploying site/ to $SERVER:$SERVER_SITE_DIR …"
  ssh -i "$SSH_KEY" "$SERVER" "sudo mkdir -p $SERVER_SITE_DIR && sudo chown ubuntu:ubuntu $SERVER_SITE_DIR"
  rsync -avz --delete -e "ssh -i $SSH_KEY" site/ "$SERVER:$SERVER_SITE_DIR/"
  echo "✓ Site deployed."
fi

echo ""
echo "✓ Release v$NEW_VERSION shipped."
echo "  GitHub:   https://github.com/polistician/macsweep/releases/tag/v$NEW_VERSION"
echo "  Site:     https://macsweep.polistician.ai/"
echo "  In-app:   Settings → Check for updates → Install v$NEW_VERSION"
