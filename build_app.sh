#!/usr/bin/env bash
# Build a native MacSweep.app bundle. Finder launches .app directly with no
# Terminal involved. This is just the bundle wrapper — the actual app runs
# from the parent directory's launch.py via the project's .venv.
set -euo pipefail
cd "$(dirname "$0")"

APP="MacSweep.app"
PROJECT="$(pwd)"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# Info.plist — declares this is a regular GUI app
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>MacSweep</string>
  <key>CFBundleDisplayName</key><string>MacSweep</string>
  <key>CFBundleExecutable</key><string>MacSweep</string>
  <key>CFBundleIdentifier</key><string>com.macsweep.app</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleSignature</key><string>????</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>LSUIElement</key><false/>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSPrincipalClass</key><string>NSApplication</string>
</dict>
</plist>
EOF

# The launcher that the .app actually runs.
# Hard-codes the project path so the .app can be moved anywhere on disk.
cat > "$APP/Contents/MacOS/MacSweep" <<EOF
#!/usr/bin/env bash
PROJECT="$PROJECT"
cd "\$PROJECT" || {
  /usr/bin/osascript -e 'display dialog "MacSweep project folder not found at $PROJECT. Re-run build_app.sh from the project directory." buttons {"OK"} with icon stop with title "MacSweep"'
  exit 1
}

# Hard-kill any prior MacSweep instance so a fresh launch always picks up the
# latest code. macOS's LaunchServices may otherwise just refocus the existing
# window without re-running this script.
pkill -9 -f "$PROJECT/launch.py" 2>/dev/null || true
sleep 0.3

mkdir -p data

# First-time setup — silent. If we have to install, show a non-blocking notification.
if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c 'import webview' >/dev/null 2>&1; then
  /usr/bin/osascript -e 'display notification "Installing dependencies (one-time, ~30s)…" with title "MacSweep"' >/dev/null 2>&1 &
  /usr/bin/python3 -m venv .venv >> data/setup.log 2>&1 || true
  .venv/bin/pip install --quiet --upgrade pip >> data/setup.log 2>&1
  .venv/bin/pip install --quiet -r requirements.txt >> data/setup.log 2>&1
  if ! .venv/bin/python -c 'import webview' >/dev/null 2>&1; then
    /usr/bin/osascript -e 'display dialog "MacSweep failed to install dependencies. See data/setup.log in the project folder." buttons {"OK"} with icon stop with title "MacSweep"'
    exit 1
  fi
fi

exec ./.venv/bin/python launch.py >> data/launch.log 2>&1
EOF
chmod +x "$APP/Contents/MacOS/MacSweep"

echo "Built $APP/"
ls -la "$APP/Contents/"
