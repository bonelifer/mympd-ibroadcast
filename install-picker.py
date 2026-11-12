#!/usr/bin/env python3
"""Install the iBroadcast picker using myMPD's protected JSON-RPC API."""

import argparse
import json
import os
import shutil
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


def call_api(endpoint, headers, payload):
    """Send a JSON-RPC request and return the successful result."""
    request = Request(endpoint, json.dumps(payload).encode(), headers)
    with urlopen(request, timeout=15) as response:
        data = json.load(response)
    if "result" not in data or "error" in data:
        raise ValueError(f"myMPD rejected {payload['method']}: {data.get('error')}")
    return data["result"]


def install_home(endpoint, headers, launcher, icon, thumbs):
    """Copy the bundled icon and update the matching Home tile in place."""
    params = launcher["params"]
    current = call_api(endpoint, headers, {
        "jsonrpc": "2.0", "id": 1, "method": "MYMPD_API_HOME_ICON_LIST", "params": {},
    })
    for position, tile in enumerate(current.get("data") or []):
        if (tile.get("type") == "icon" and tile.get("name") == params["name"]
                and tile.get("cmd") == params["cmd"]
                and tile.get("options", [])[:1] == params["options"][:1]):
            params.update(replace=True, oldpos=position)
            for key in ("ligature", "bgcolor", "color", "options"):
                if key in tile:
                    params[key] = tile[key]
            break
    destination = thumbs / icon.name
    if destination.is_symlink():
        raise OSError(f"Refusing to overwrite symlink: {destination}")
    shutil.copyfile(icon, destination)
    destination.chmod(0o644)
    call_api(endpoint, headers, launcher)


def main():
    """Submit the bundled Lua script to the selected myMPD partition."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mympd", required=True, help="myMPD base URL")
    parser.add_argument("--partition", default="default")
    parser.add_argument("--service", default="http://127.0.0.1:18769",
                        help="Bridge URL reachable from myMPD")
    parser.add_argument("--replace", action="store_true",
                        help="Replace the installed script of the same name")
    parser.add_argument("--workdir", type=Path,
                        help="Local myMPD work directory for installing the Home tile")
    args = parser.parse_args()
    bundle = Path(__file__).resolve().parent
    if args.workdir is not None:
        thumbs = args.workdir / "pics" / "thumbs"
        if not thumbs.is_dir():
            parser.error("--workdir must contain myMPD's pics/thumbs directory")
        try:
            launcher = json.loads((bundle / "home-launcher.json").read_text(
                encoding="utf-8"
            ))
            icon = bundle / "assets" / launcher["params"]["image"]
            if not icon.is_file():
                raise FileNotFoundError(icon)
        except (OSError, KeyError, ValueError) as error:
            parser.exit(1, f"Could not load the bundled Home tile: {error}\n")
    for url in (args.mympd, args.service):
        parsed = urlsplit(url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.query or parsed.fragment
                or not url.isascii() or any(ord(char) <= 32 for char in url)):
            parser.error("URLs must be HTTP(S) without credentials or queries")

    filename = "ibroadcast.lua"
    default_service = "http://127.0.0.1:18769"
    metadata_line, _, source = Path(__file__).with_name(filename).read_text(
        encoding="utf-8"
    ).partition("\n")
    metadata = json.loads(metadata_line.removeprefix("-- "))
    source = source.replace(
        json.dumps(default_service), json.dumps(args.service.rstrip("/")), 1
    )
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "MYMPD_API_SCRIPT_SAVE",
        "params": {
            "script": metadata["name"],
            "oldscript": metadata["name"] if args.replace else "",
            "file": metadata["file"],
            "version": metadata["version"],
            "order": metadata["order"],
            "arguments": metadata["arguments"],
            "content": source,
        },
    }
    headers = {"Content-Type": "application/json"}
    if session := os.environ.get("MYMPD_SESSION"):
        headers["X-myMPD-Session"] = session
    endpoint = args.mympd.rstrip("/") + "/api/" + quote(args.partition, safe="")
    try:
        call_api(endpoint, headers, payload)
        print(f"Installed {metadata['name']} in myMPD.")
        if args.workdir is not None:
            install_home(endpoint, headers, launcher, icon, thumbs)
            print(f"Installed the {metadata['name']} Home tile and icon.")
    except (OSError, ValueError) as error:
        parser.exit(1, f"Installation failed: {error}\n")



if __name__ == "__main__":
    main()
