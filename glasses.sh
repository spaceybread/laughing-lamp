#!/usr/bin/env bash
# Helper for pointing the INMO Air2 at the broadcast page.
#
#   ./glasses.sh            # USB: adb reverse + open http://localhost:3000/broadcast on the glasses
#   ./glasses.sh wifi <ip>  # switch glasses to Wi-Fi ADB (adb tcpip), then same as above
#   ./glasses.sh check      # show device, Android version, installed browsers, cameras
#   ./glasses.sh watch      # keep re-adding the port-forward whenever the glasses reconnect (flaky cable)
#
# Env: PORT (default 3000), BROWSER (package name to force, e.g. org.mozilla.firefox),
#      GLASSES_IP (when set, every command first does `adb connect $GLASSES_IP:5555`, i.e. Wi-Fi ADB)
set -euo pipefail
PORT="${PORT:-3000}"
PAGE="${PAGE:-hud}"              # hud (split heads-up display) or broadcast (plain preview)
URL="http://localhost:${PORT}/${PAGE}?autostart=1"

cmd="${1:-usb}"
if [[ -n "${GLASSES_IP:-}" && "$cmd" != "wifi" ]]; then
  export ANDROID_SERIAL="${GLASSES_IP}:5555"   # target the Wi-Fi link even if USB is also plugged in
  adb connect "${GLASSES_IP}:5555" >/dev/null 2>&1 || true
fi
case "$cmd" in
  wifi)
    IP="${2:?usage: ./glasses.sh wifi <glasses-ip>}"
    adb tcpip 5555          # needs the USB cable once; resets when the glasses reboot
    sleep 3
    adb connect "${IP}:5555"
    echo "Now unplug USB. For later commands: export GLASSES_IP=${IP}"
    ;;
  check)
    adb devices -l
    echo "Android: $(adb shell getprop ro.build.version.release)  model: $(adb shell getprop ro.product.model)"
    echo "Glasses IP: $(adb shell ip -4 addr show wlan0 2>/dev/null | awk '/inet /{print $2}' || true)"
    echo "Browser-ish packages:"
    adb shell pm list packages | grep -iE 'chrome|chromium|browser|firefox|webview|brave|kiwi' || echo "  (none found – sideload one, see README)"
    echo "Cameras:"
    adb shell dumpsys media.camera 2>/dev/null | grep -E 'Camera [0-9]+|Facing|Device version' | head -20 || true
    exit 0
    ;;
  watch)
    echo "Watching for the glasses; re-adding adb reverse on every reconnect (Ctrl-C to stop)"
    while true; do
      if [[ -n "${GLASSES_IP:-}" ]]; then
        until adb connect "${GLASSES_IP}:5555" 2>/dev/null | grep -qE "^(connected|already)"; do sleep 3; done
      fi
      adb wait-for-device >/dev/null 2>&1
      adb reverse "tcp:${PORT}" "tcp:${PORT}" >/dev/null 2>&1 && echo "$(date +%T) glasses connected, reverse tcp:${PORT} set"
      adb wait-for-disconnect >/dev/null 2>&1
      echo "$(date +%T) glasses disconnected"
    done
    ;;
esac

adb wait-for-device
adb reverse "tcp:${PORT}" "tcp:${PORT}"
echo "adb reverse tcp:${PORT} -> Mac tcp:${PORT} (glasses see the server as localhost)"

if [[ -n "${BROWSER:-}" ]]; then
  adb shell am start -a android.intent.action.VIEW -d "$URL" "$BROWSER"
else
  adb shell am start -a android.intent.action.VIEW -d "$URL"
fi
# the stock browser asks for the camera on every load: accept it for our own page.
# Only when the browser is the focused window, and only by tapping the Allow button's own bounds.
for i in 1 2 3 4 5 6; do
  sleep 1
  adb shell dumpsys window 2>/dev/null | grep -q "mCurrentFocus=.*com.android.browser" || continue
  dump=$(adb exec-out uiautomator dump /dev/tty 2>/dev/null)
  bounds=$(printf '%s' "$dump" | grep -oE 'text="Allow"[^>]*bounds="\[[0-9]+,[0-9]+\]\[[0-9]+,[0-9]+\]"' | grep -oE '\[[0-9]+,[0-9]+\]\[[0-9]+,[0-9]+\]' | head -1)
  if [[ -n "$bounds" ]]; then
    read x1 y1 x2 y2 < <(echo "$bounds" | tr -c '0-9' ' ')
    adb shell input tap $(( (x1 + x2) / 2 )) $(( (y1 + y2) / 2 )); echo "Accepted the camera prompt."; break
  fi
done
echo "Opened $URL on the glasses."
