#!/bin/zsh
set -e

SOURCE_APP="/Users/matt/walkingpad/macos/WalkingPad.app"
TARGET_APP="/Applications/WalkingPad.app"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "This installer writes to /Applications and needs sudo."
  echo "Run: sudo /Users/matt/walkingpad/scripts/install_app_system.sh"
  exit 1
fi

rm -rf "$TARGET_APP"
cp -R "$SOURCE_APP" "$TARGET_APP"
chown -R root:wheel "$TARGET_APP"
touch "$TARGET_APP"

echo "Installed WalkingPad.app to /Applications"
