#!/usr/bin/env python3
"""Run where the Hermes key already lives (the container with ~/.config/rclone/sa.json). Prints the block to paste into the
cloud environment's Environment variables box. The key is base64-encoded onto one line, so the .env box cannot mangle it."""
import base64, json, os, sys
p = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/.config/rclone/sa.json")
raw = open(p).read()
json.loads(raw)                                   # refuse anything that is not a JSON key file
print("CLIENTS_DRIVE_ID=0AGyf2DLZP-r8Uk9PVA")
print("OPS_DRIVE_ID=0AFBBEThGl8jnUk9PVA")
print("GDRIVE_SA_JSON_B64=" + base64.b64encode(raw.strip().encode()).decode())
