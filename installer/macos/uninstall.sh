#!/bin/bash
# Clean uninstall for a Workflow Recorder macOS deployment.
#
# macOS .pkg installs don't provide an uninstall action — this script
# replaces that. Run it as the user whose install you want to remove
# (it does NOT need sudo for user-level removal; use sudo only if the
# app ended up in /Applications/ and you're not the original installer).
#
# Usage:
#   bash uninstall.sh            # remove agent + app bundle, keep config/logs
#   bash uninstall.sh --purge    # also delete config + logs

set -u

PURGE="no"
if [ "${1:-}" = "--purge" ]; then PURGE="yes"; fi

TARGET_USER="${USER:-$(stat -f '%Su' /dev/console)}"
TARGET_UID=$(id -u "$TARGET_USER")
TARGET_HOME=$(dscl . -read "/Users/$TARGET_USER" NFSHomeDirectory 2>/dev/null | awk '{print $2}')
[ -z "$TARGET_HOME" ] && TARGET_HOME="/Users/$TARGET_USER"

AGENT_FILE="$TARGET_HOME/Library/LaunchAgents/com.workflow-recorder.plist"

echo "==> Unloading LaunchAgent"
launchctl bootout "gui/${TARGET_UID}/com.workflow-recorder" 2>/dev/null || \
    launchctl unload "$AGENT_FILE" 2>/dev/null || true
rm -f "$AGENT_FILE"

echo "==> Removing /Applications/WorkflowRecorder.app"
if [ -d /Applications/WorkflowRecorder.app ]; then
    sudo rm -rf /Applications/WorkflowRecorder.app
fi

echo "==> Forgetting installer receipt"
sudo pkgutil --forget com.workflow-recorder.pkg 2>/dev/null || true

if [ "$PURGE" = "yes" ]; then
    echo "==> Purging config + logs"
    rm -rf "$TARGET_HOME/Library/Application Support/WorkflowRecorder"
    rm -rf "$TARGET_HOME/Library/Logs/WorkflowRecorder"
fi

echo "==> Done. Reminder: revoke Screen Recording / Accessibility grants"
echo "    in System Settings → Privacy & Security if desired."
