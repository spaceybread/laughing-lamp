#!/usr/bin/env python3
"""Join the INMO Air2 (or any Android 9-ish device) to a Wi-Fi network over USB ADB, no typing on the glasses.

    ./glasses_wifi.py "My Network"                 # prompts for the password (not echoed, not in shell history)
    ./glasses_wifi.py "My Network" --adb-wifi      # ...then switch ADB to Wi-Fi and print GLASSES_IP
    ./glasses_wifi.py "My Network" -p secret       # password on the command line (visible in history)

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
        if got:
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


def join(ssid, password, timeout=40):
    shell("svc wifi enable", check=False)
    shell("am start -a android.settings.WIFI_SETTINGS >/dev/null", check=False)
    time.sleep(2)

    # 1) the network in the scan list?
    def find_ssid(root):
        exact = [n for n in nodes(root, text=ssid) if n.get("text", "").lower() == ssid.lower()]
        return exact[0] if exact else None
    node, root = wait_for(find_ssid, 12)
    if node is None:
        # scroll the list once and look again
        shell("input swipe 640 650 640 250 300", check=False); time.sleep(1.5)
        node, root = wait_for(find_ssid, 6)

    if node is not None:
        print(f"found '{ssid}' in the list, opening it")
        tap(node)
    else:
        add = (nodes(ui_dump(), text="Add network") or [None])[0]
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

    # 2) password field (absent if the network is already saved) and CONNECT
    root = ui_dump()
    pw = [n for n in nodes(root, **{"class": "android.widget.EditText"}) if n.get("password") == "true"]
    if not pw:   # Add-network dialog: the password box is the second EditText
        edits = nodes(root, **{"class": "android.widget.EditText"})
        pw = edits[1:2] if len(edits) > 1 else []
    if pw:
        print("typing the password")
        tap(pw[0]); type_text(password)
        shell("input keyevent 111", check=False)       # ESC: hide the keyboard so the buttons are reachable
        time.sleep(0.5)
    elif nodes(root, text="FORGET"):
        print("network already saved on the glasses; connecting without a password")
    btn = (nodes(ui_dump(), text="CONNECT") or nodes(ui_dump(), text="SAVE") or [None])[0]
    if btn is None:
        raise RuntimeError("no CONNECT button found; the dialog looks different than expected")
    tap(btn)

    # 3) wait for an address
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
    password = a.password if a.password is not None else getpass.getpass(f"Password for '{a.ssid}': ")
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
