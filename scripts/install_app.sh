#!/bin/zsh
set -e

SOURCE_APP="/Users/matt/walkingpad/macos/WalkingPad.app"
USER_APPS="$HOME/Applications"

mkdir -p "$USER_APPS"
rm -rf "$USER_APPS/WalkingPad.app"
cp -R "$SOURCE_APP" "$USER_APPS/WalkingPad.app"
touch "$USER_APPS/WalkingPad.app"

echo "Installed WalkingPad.app to $USER_APPS"
echo "Open it from Applications, or drag it from Applications to the Dock."
