#!/usr/bin/env python3
"""Join the INMO Air2 (or any Android 9-ish device) to a Wi-Fi network over USB ADB, no typing on the glasses.

    glasses/glasses_wifi.py "My Network"                 # prompts for the password if needed (not echoed, not in history)
    glasses/glasses_wifi.py "My Network" --adb-wifi      # ...then switch ADB to Wi-Fi and print GLASSES_IP
    glasses/glasses_wifi.py "My Network" -p secret       # password on the command line (visible in history)

How: opens the stock Wi-Fi settings screen, reads the UI hierarchy with `uiautomator dump`, taps the
network by name (or "Add network" if it isn't listed), types the password into the password field,
taps CONNECT, waits for an IP address, and returns to the home screen. Needs developer mode / ADB on
the glasses and the magnetic USB cable (it carries data).
"""
import argparse
import getpass
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ADB = ["adb"]


def sh(*args, check=True, timeout=30):
    r = subprocess.run(ADB + list(args), capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"adb {' '.join(args)} failed: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def shell(cmd, **kw):
    return sh("shell", cmd, **kw)


def pick_usb_device(serial):
    if serial:
        ADB.extend(["-s", serial]); return serial
    lines = [l.split() for l in sh("devices").splitlines()[1:] if l.strip()]
    usb = [l[0] for l in lines if len(l) > 1 and l[1] == "device" and ":" not in l[0]]
    if not usb:
        others = [l[0] for l in lines if len(l) > 1 and l[1] == "device"]
        if others:
            print(f"no USB device, using {others[0]}"); ADB.extend(["-s", others[0]]); return others[0]
        raise SystemExit("No device over adb. Plug the magnetic cable into the Mac, make sure developer mode is on, "
                         "and accept 'Allow USB debugging' on the glasses if asked.")
    ADB.extend(["-s", usb[0]]); return usb[0]


# ---- UI hierarchy helpers ---------------------------------------------------------------------
def ui_dump():
    """Return the current screen's view hierarchy as an ElementTree root."""
    out = sh("exec-out", "uiautomator", "dump", "/dev/tty", check=False, timeout=20)
    m = re.search(r"<\?xml.*</hierarchy>", out, re.S)
    if not m:                                       # some builds refuse /dev/tty: go through a file
        shell("uiautomator dump /sdcard/.ui.xml >/dev/null 2>&1", check=False)
        out = shell("cat /sdcard/.ui.xml; rm -f /sdcard/.ui.xml", check=False)
        m = re.search(r"<\?xml.*</hierarchy>", out, re.S)
    if not m:
        raise RuntimeError("could not read the UI hierarchy (uiautomator dump)")
    return ET.fromstring(m.group(0))


def nodes(root, **attrs):
    """All nodes whose attributes contain the given values (substring, case-insensitive for text)."""
    out = []
    for n in root.iter("node"):
        ok = True
        for k, v in attrs.items():
            a = n.get(k, "")
            if k in ("text", "content-desc"):
                ok &= v.lower() == a.lower() or v.lower() in a.lower()
            else:
                ok &= v in a
        if ok:
            out.append(n)
    return out


def centre(node):
    x1, y1, x2, y2 = map(int, re.findall(r"\d+", node.get("bounds")))
    return (x1 + x2) // 2, (y1 + y2) // 2


def tap(node):
    x, y = centre(node); shell(f"input tap {x} {y}"); time.sleep(0.8)


def type_text(text):
    """adb `input text` wants spaces as %s and must survive the device shell's quoting."""
    esc = text.replace("%", "\\%").replace(" ", "%s")
    esc = esc.replace("'", "'\"'\"'")
    shell(f"input text '{esc}'"); time.sleep(0.5)


def wait_for(predicate, seconds, every=1.0):
    end = time.time() + seconds
    while time.time() < end:
        root = ui_dump()
        got = predicate(root)
        if got is not None:
            return got, root
        time.sleep(every)
    return None, None


# ---- the flow ----------------------------------------------------------------------------------
def current_connection():
    info = shell("dumpsys wifi", check=False, timeout=20)
    m = re.search(r"mWifiInfo SSID: (.*?), BSSID.*?Supplicant state: (\w+)", info)
    ip = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", shell("ip -4 addr show wlan0", check=False))
    ssid = m.group(1).strip('"') if m else None
    return ssid, (m.group(2) if m else None), (ip.group(1) if ip else None)


def find_password_field(tries=4):
    """The password box can be hidden under the keyboard or below the fold of the dialog."""
    for _ in range(tries):
        root = ui_dump()                     # (no ESC/back here: on this dialog it closes the dialog)
        pw = [n for n in nodes(root, **{"class": "android.widget.EditText"}) if n.get("password") == "true"]
        if pw:
            return pw[0]
        edits = nodes(root, **{"class": "android.widget.EditText"})
        if len(edits) > 1:
            return edits[1]
        if len(edits) == 1 and not nodes(root, text="Network name"):
            return edits[0]                  # join dialog: its only text box is the password
        # scroll inside the dialog: swipe up over its lower half
        dlg = [n for n in root.iter("node") if n.get("scrollable") == "true"]
        x1, y1, x2, y2 = map(int, re.findall(r"\d+", (dlg[0] if dlg else root.find("node")).get("bounds")))
        x = (x1 + x2) // 2
        shell(f"input swipe {x} {int(y1 + (y2 - y1) * 0.85)} {x} {int(y1 + (y2 - y1) * 0.35)} 500", check=False)
        time.sleep(0.8)
    return None


def join(ssid, password, timeout=40):
    shell("svc wifi enable", check=False)
    shell("input keyevent HOME", check=False); time.sleep(0.5)             # start from a clean screen
    shell("am start -a android.settings.WIFI_SETTINGS >/dev/null", check=False)
    time.sleep(2)

    # 1) the network in the scan list? (only visible rows are in a dump: scroll through the list)
    def visible_texts(root):
        return [n.get("text") for n in root.iter("node") if n.get("text")]

    def scroll_down(root):
        sc = [n for n in root.iter("node") if n.get("scrollable") == "true"]
        if sc:
            x1, y1, x2, y2 = map(int, re.findall(r"\d+", sc[0].get("bounds")))
        else:
            x1, y1, x2, y2 = 0, 200, 1280, 760
        x = (x1 + x2) // 2
        shell(f"input swipe {x} {int(y1 + (y2 - y1) * 0.8)} {x} {int(y1 + (y2 - y1) * 0.25)} 600", check=False)
        time.sleep(1.2)

    def find_text(label, exact=True, max_scrolls=8, settle=10):
        """Scroll through the settings list until a row with this text is visible; returns the node or None."""
        deadline = time.time() + settle
        seen_same = 0
        for _ in range(max_scrolls + 20):
            root = ui_dump()
            hits = [n for n in nodes(root, text=label) if (n.get("text", "").lower() == label.lower()) or not exact]
            if hits:
                return hits[0]
            before = visible_texts(root)
            scroll_down(root)
            after = visible_texts(ui_dump())
            if after == before:                       # end of list (or list not ready yet while scanning)
                seen_same += 1
                if time.time() > deadline and seen_same >= 2:
                    return None
                time.sleep(1.0)
        return None

    node = find_text(ssid)
    if node is not None:
        print(f"found '{ssid}' in the list, opening it")
        tap(node)
    else:
        add = find_text("Add network")
        if add is None:
            raise RuntimeError(f"'{ssid}' is not in the scan list and there is no 'Add network' entry")
        print(f"'{ssid}' not in the list; using Add network")
        tap(add)
        root = ui_dump()
        field = (nodes(root, **{"class": "android.widget.EditText"}) or [None])[0]
        if field is None:
            raise RuntimeError("Add network: no name field")
        tap(field); type_text(ssid)
        spinner = (nodes(root, **{"class": "android.widget.Spinner"}) or [None])[0]
        if spinner is not None:
            tap(spinner)
            opt = (nodes(ui_dump(), text="WPA/WPA2") or nodes(ui_dump(), text="WPA2") or [None])[0]
            if opt is None:
                raise RuntimeError("Add network: could not pick WPA/WPA2 security")
            tap(opt)

    # 2) a saved network in range connects straight away when tapped (no dialog at all); otherwise a
    #    dialog: password field (absent if the network is saved but out of range), then CONNECT / SAVE
    time.sleep(1.5)
    s, _, _ = current_connection()
    if s and s.lower() == ssid.lower():
        print("network already saved on the glasses; it is connecting")
        return wait_connected(ssid, timeout)
    root = ui_dump()
    saved_dialog = bool(nodes(root, text="FORGET")) and not nodes(root, **{"class": "android.widget.EditText"})
    if saved_dialog:
        print("network already saved on the glasses; connecting without a password")
    else:
        if password is None:
            password = getpass.getpass(f"Password for '{ssid}': ")
        pw = find_password_field()
        if pw is None:
            raise RuntimeError("could not find the password field in the dialog")
        print("typing the password")
        tap(pw); time.sleep(1.0)              # typed straight after the tap, the first character gets lost
        type_text(password)
        # the field shows one dot per character: check nothing was dropped, retype once if it was
        for attempt in range(2):
            got = [n for n in nodes(ui_dump(), **{"class": "android.widget.EditText"}) if n.get("password") == "true"]
            if not got or len(got[0].get("text", "")) == len(password):
                break
            print(f"password field has {len(got[0].get('text', ''))} characters, expected {len(password)}; retyping")
            tap(got[0]); time.sleep(1.0)
            shell("input keyevent 123 " + " ".join(["67"] * (len(password) + 10)), check=False)   # MOVE_END + DELs
            time.sleep(0.8); type_text(password)
    btn = None
    for _ in range(3):
        root = ui_dump()
        btn = (nodes(root, text="CONNECT") or nodes(root, text="SAVE") or [None])[0]
        if btn is not None and btn.get("enabled", "true") == "true":
            break
        time.sleep(0.7)
    if btn is None:
        raise RuntimeError("no CONNECT/SAVE button found; the dialog looks different than expected")
    if btn.get("enabled") == "false":
        raise RuntimeError("the CONNECT/SAVE button stayed disabled (password too short?)")
    tap(btn)
    return wait_connected(ssid, timeout)


def wait_connected(ssid, timeout):
    """3) wait for an address on the wanted network"""
    print("waiting for the connection", end="", flush=True)
    end = time.time() + timeout
    while time.time() < end:
        s, st, ip = current_connection()
        if ip and s and s.lower() == ssid.lower():
            print(f"\nconnected to '{s}' with IP {ip}")
            return ip
        print(".", end="", flush=True); time.sleep(2)
    s, st, ip = current_connection()
    raise RuntimeError(f"\nnot connected after {timeout}s (state {st}, ssid {s}). Wrong password? The Air2 needs 2.4 GHz WPA2.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("ssid")
    ap.add_argument("-p", "--password", help="Wi-Fi password (omit to be prompted)")
    ap.add_argument("-s", "--serial", help="adb serial to use (default: the USB device)")
    ap.add_argument("--adb-wifi", action="store_true", help="afterwards switch ADB to TCP (adb tcpip 5555) and connect over Wi-Fi")
    ap.add_argument("--timeout", type=int, default=40)
    a = ap.parse_args()
    password = a.password                     # asked for only if the glasses don't know the network yet
    serial = pick_usb_device(a.serial)
    print(f"device {serial}")
    s, st, ip = current_connection()
    if ip and s and s.lower() == a.ssid.lower():
        print(f"already connected to '{s}' with IP {ip}")
    else:
        ip = join(a.ssid, password, a.timeout)
    shell("input keyevent HOME", check=False)
    if a.adb_wifi:
        sh("tcpip", "5555"); time.sleep(3)
        r = subprocess.run(["adb", "connect", f"{ip}:5555"], capture_output=True, text=True)
        print(r.stdout.strip())
        print(f"\nexport GLASSES_IP={ip}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr); sys.exit(1)
