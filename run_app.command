#!/bin/zsh
set -euo pipefail

ROOT="${0:A:h}"
BUILD_DIR="$ROOT/.build"
APP_BINARY="$BUILD_DIR/SurfTracker"
APP_BUNDLE="$BUILD_DIR/Surf Tracker.app"
APP_CONTENTS="$APP_BUNDLE/Contents"
APP_ICON="$APP_CONTENTS/Resources/AppIcon.icns"
ICON_SOURCE="$BUILD_DIR/AppIcon-1024.png"
ICONSET="$BUILD_DIR/AppIcon.iconset"
ICON_GENERATOR="$BUILD_DIR/GenerateIcon"
SERVER_LOG="$BUILD_DIR/review-server.log"

mkdir -p "$BUILD_DIR"

if [[ ! -x "$APP_BINARY" || "$ROOT/desktop/SurfTracker.swift" -nt "$APP_BINARY" ]]; then
  swiftc "$ROOT/desktop/SurfTracker.swift" \
    -framework Cocoa \
    -framework WebKit \
    -framework UniformTypeIdentifiers \
    -o "$APP_BINARY"
fi

if [[ ! -f "$APP_ICON" || "$ROOT/desktop/GenerateIcon.swift" -nt "$APP_ICON" ]]; then
  swiftc "$ROOT/desktop/GenerateIcon.swift" -framework Cocoa -o "$ICON_GENERATOR"
  "$ICON_GENERATOR" "$ICON_SOURCE"
  mkdir -p "$ICONSET"
  for spec in "16:icon_16x16.png" "32:icon_16x16@2x.png" \
    "32:icon_32x32.png" "64:icon_32x32@2x.png" \
    "128:icon_128x128.png" "256:icon_128x128@2x.png" \
    "256:icon_256x256.png" "512:icon_256x256@2x.png" \
    "512:icon_512x512.png" "1024:icon_512x512@2x.png"; do
    sips -z "${spec%%:*}" "${spec%%:*}" "$ICON_SOURCE" \
      --out "$ICONSET/${spec#*:}" >/dev/null
  done
  mkdir -p "$APP_CONTENTS/Resources"
  iconutil -c icns "$ICONSET" -o "$APP_ICON"
fi

mkdir -p "$APP_CONTENTS/MacOS" "$APP_CONTENTS/Resources"
cp "$APP_BINARY" "$APP_CONTENTS/MacOS/SurfTracker"
cp "$ROOT/desktop/Info.plist" "$APP_CONTENTS/Info.plist"
codesign --force --deep --sign - "$APP_BUNDLE" >/dev/null

if ! curl --silent --fail --max-time 1 http://127.0.0.1:8765/api/session >/dev/null 2>&1; then
  "$ROOT/.venv/bin/python" "$ROOT/review_server.py" >"$SERVER_LOG" 2>&1 &
  SERVER_PID=$!
  trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT
  for _ in {1..40}; do
    if curl --silent --fail --max-time 1 http://127.0.0.1:8765/api/session >/dev/null 2>&1; then
      break
    fi
    read -t 0.1 || true
  done
fi

open -W -n "$APP_BUNDLE"
