#!/bin/zsh
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
icon_svg="$repo_root/assets/app-icon.svg"
install_root="${1:-$HOME/Applications}"
app_path="$install_root/Claude Recent Sync.app"
python_bin="${PYTHON_BIN:-$(command -v python3)}"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

"$python_bin" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || {
  print -u2 "Python 3.10 or newer is required."
  exit 1
}

mkdir -p "$install_root"

cat > "$work_dir/launcher.applescript" <<APPLESCRIPT
on run
  set launcherPath to POSIX path of (path to resource "claude-recent-sync-ui" in directory "bin")
  set logPath to ((path to home folder as text) & ".claude:claude-recent-sync:launcher.log")
  do shell script "mkdir -p " & quoted form of POSIX path of ((path to home folder as text) & ".claude:claude-recent-sync")
  do shell script "nohup " & quoted form of launcherPath & " --no-browser >" & quoted form of POSIX path of logPath & " 2>&1 &"
  delay 0.8
  open location "http://127.0.0.1:47631"
end run
APPLESCRIPT

rm -rf "$app_path"
osacompile -o "$app_path" "$work_dir/launcher.applescript"

resources="$app_path/Contents/Resources"
mkdir -p "$resources/bin" "$resources/runtime"
ditto --norsrc "$repo_root/src/claude_recent_sync" "$resources/runtime/claude_recent_sync"
find "$resources/runtime" -type d -name "__pycache__" -prune -exec rm -rf {} +
cat > "$resources/bin/claude-recent-sync-ui" <<LAUNCHER
#!/bin/zsh
set -euo pipefail
resources="\$(cd "\$(dirname "\$0")/.." && pwd)"
export PYTHONPATH="\$resources/runtime"
export PYTHONDONTWRITEBYTECODE=1
exec "$python_bin" -m claude_recent_sync.server "\$@"
LAUNCHER
chmod +x "$resources/bin/claude-recent-sync-ui"

mkdir -p "$work_dir/icon-preview"
qlmanage -t -s 1024 -o "$work_dir/icon-preview" "$icon_svg" >/dev/null 2>&1
icon_png="$work_dir/icon-preview/app-icon.svg.png"
if [[ -f "$icon_png" ]]; then
  iconset="$work_dir/AppIcon.iconset"
  mkdir -p "$iconset"
  sips -z 16 16 "$icon_png" --out "$iconset/icon_16x16.png" >/dev/null
  sips -z 32 32 "$icon_png" --out "$iconset/icon_16x16@2x.png" >/dev/null
  sips -z 32 32 "$icon_png" --out "$iconset/icon_32x32.png" >/dev/null
  sips -z 64 64 "$icon_png" --out "$iconset/icon_32x32@2x.png" >/dev/null
  sips -z 128 128 "$icon_png" --out "$iconset/icon_128x128.png" >/dev/null
  sips -z 256 256 "$icon_png" --out "$iconset/icon_128x128@2x.png" >/dev/null
  sips -z 256 256 "$icon_png" --out "$iconset/icon_256x256.png" >/dev/null
  sips -z 512 512 "$icon_png" --out "$iconset/icon_256x256@2x.png" >/dev/null
  sips -z 512 512 "$icon_png" --out "$iconset/icon_512x512.png" >/dev/null
  cp "$icon_png" "$iconset/icon_512x512@2x.png"
  iconutil -c icns "$iconset" -o "$app_path/Contents/Resources/applet.icns"
  touch "$app_path"
fi

plutil -replace CFBundleName -string "Claude Recent Sync" "$app_path/Contents/Info.plist"
plutil -replace CFBundleDisplayName -string "Claude Recent Sync" "$app_path/Contents/Info.plist"
plutil -replace CFBundleIdentifier -string "com.beiciccc.claude-recent-sync" "$app_path/Contents/Info.plist"
plutil -replace LSMinimumSystemVersion -string "12.0" "$app_path/Contents/Info.plist"
codesign --force --deep --sign - --timestamp=none "$app_path" >/dev/null

printf '%s\n' "$app_path"
