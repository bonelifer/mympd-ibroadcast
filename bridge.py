#!/usr/bin/env python3
"""Browse and stream one iBroadcast library on a private myMPD listener."""

import argparse
import json
import os
import random
import re
import shutil
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

OAUTH = "https://oauth.ibroadcast.com"
CLIENT = "mympd-ibroadcast"
AGENT = f"{CLIENT}/1.0"


def token_request(client_id: str, grant: str, **fields) -> dict:
    """Exchange a device or refresh grant for tokens."""
    data = urlencode(dict(fields, client_id=client_id, grant_type=grant))
    try:
        with urlopen(OAUTH + "/token", data.encode(), timeout=15) as response:
            return json.load(response)
    except HTTPError as error:
        if error.code in (400, 429):
            with error:
                return json.load(error)
        raise


def save_token(path: Path, token: dict) -> dict:
    """Store rotating credentials atomically with private permissions."""
    token["expires_at"] = time.time() + int(token["expires_in"])
    with tempfile.NamedTemporaryFile(
        mode="w", dir=path.parent, delete=False, encoding="utf-8"
    ) as output:
        temporary = Path(output.name)
        try:
            json.dump(token, output)
            output.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return token


def authorize(client_id: str, path: Path, write: bool) -> None:
    """Guide the owner through the documented OAuth device flow."""
    query = urlencode({
        "client_id": client_id,
        "scope": "user.account:read user.library:read"
        + (" user.library:write" if write else ""),
    })
    with urlopen(f"{OAUTH}/device/code?{query}", timeout=15) as response:
        device = json.load(response)
    print(f"Open {device['verification_uri']} and enter {device['user_code']}.",
          flush=True)
    interval = max(1, int(device["interval"]))
    deadline = time.monotonic() + int(device["expires_in"])
    while time.monotonic() + interval < deadline:
        time.sleep(interval)
        token = token_request(
            client_id, "device_code", device_code=device["device_code"]
        )
        error = token.get("error")
        if error == "slow_down":
            interval += 5
        elif error != "authorization_pending":
            if error:
                raise ValueError("iBroadcast authorization failed")
            save_token(path, token)
            print("iBroadcast authorized.")
            return
    raise ValueError("iBroadcast authorization expired")


def cache_cover(uri: str, source: str) -> bool:
    """Store a provider image using myMPD's native stream cover filename."""
    directory = os.environ.get("MYMPD_THUMBS_DIR")
    if not directory:
        return False
    base = Path(directory) / re.sub(r"[^A-Za-z0-9_-]", "_", uri)
    extensions = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
    if any(base.with_suffix("." + ext).is_file()
                           for ext in extensions.values()):
        return True
    temporary = None
    try:
        with urlopen(source, timeout=4) as response:
            extension = extensions.get(response.headers.get_content_type())
            data = response.read(2_000_001)
        if not extension or not 0 < len(data) <= 2_000_000:
            return False
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as output:
            temporary = Path(output.name)
            output.write(data)
        temporary.chmod(0o644)
        temporary.replace(base.with_suffix("." + extension))
        return True
    except OSError:
        return False
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def records(section: dict) -> dict:
    """Expand the positional arrays in an iBroadcast library section."""
    columns = section["map"]
    return {
        identifier: {name: row[index] for name, index in columns.items()
                     if type(index) is int and 0 <= index < len(row)}
        for identifier, row in section.items()
        if identifier.isascii() and identifier.isdecimal()
        and isinstance(row, list)
    }


class Library:
    """Keep OAuth credentials and a short-lived library snapshot."""

    def __init__(self, client_id: str, path: Path):
        self.client_id = client_id
        self.path = path
        self.token = json.loads(path.read_text(encoding="utf-8"))
        self.lock = Lock()
        self.updated = 0
        self.catalog = {}

    def access_token(self, refresh: bool = False) -> str:
        """Return a valid token while the library lock is held."""
        if refresh or self.token["expires_at"] < time.time() + 30:
            token = token_request(
                self.client_id, "refresh_token",
                refresh_token=self.token["refresh_token"],
            )
            if token.get("error"):
                raise ValueError("Run iBroadcast authorization again")
            self.token = save_token(self.path, token)
        return self.token["access_token"]

    def api(self, endpoint: str, mode: str, **fields) -> dict:
        """Send an authenticated iBroadcast request and retry expired auth."""
        payload = json.dumps({
            "client": CLIENT, "version": "1.0", "device_name": "myMPD",
            "user_agent": AGENT, "mode": mode, **fields,
        }).encode()
        for refresh in (False, True):
            headers = {
                "Authorization": "Bearer " + self.access_token(refresh),
                "User-Agent": AGENT, "Content-Type": "application/json",
            }
            try:
                with urlopen(Request(endpoint, payload, headers),
                             timeout=20) as response:
                    data = json.load(response)
            except HTTPError as error:
                with error:
                    if error.code != 401:
                        raise
                continue
            if data.get("authenticated") is False:
                continue
            if data.get("result") is False:
                break
            return data
        raise ValueError("iBroadcast API request failed")

    def snapshot(self) -> dict:
        """Return a library snapshot, refreshing it every five minutes."""
        if not self.catalog or time.monotonic() - self.updated >= 300:
            account = self.api("https://api.ibroadcast.com/status", "status")
            result = self.api("https://library.ibroadcast.com/", "library")
            origin = urlsplit(result["settings"]["streaming_server"])
            if (origin.scheme != "https"
                    or origin.netloc != "streaming.ibroadcast.com"
                    or origin.path not in ("", "/")
                    or origin.query or origin.fragment):
                raise ValueError("Unexpected iBroadcast streaming server")
            self.streaming_server = origin.geturl().rstrip("/")
            self.user_id = int(account["user"]["id"])
            self.catalog = {
                kind: {key: item for key, item in
                       records(result["library"].get(kind, {"map": {}})).items()
                       if not item.get("trashed")}
                for kind in ("artists", "albums", "tracks", "playlists")
            }
            self.catalog["tags"] = {
                key: item for key, item in result["library"].get("tags", {}).items()
                if not item.get("archived")
            }
            self.updated = time.monotonic()
        return self.catalog

    def stream_url(self, track_id: str, track: dict) -> str:
        """Build a signed upstream URL without exposing it to MPD."""
        path = track["file"]
        if not re.fullmatch(r"/?128/[a-fA-F0-9]+/[a-fA-F0-9]+/[0-9]+", path):
            raise ValueError("Unexpected iBroadcast media path")
        params = urlencode({
            "Expires": int(time.time() * 1000),
            "Signature": self.access_token(),
            "file_id": track_id,
            "user_id": self.user_id, "platform": CLIENT, "version": "1.0",
        })
        return f"{self.streaming_server}/{path.lstrip('/')}?{params}"


def track_choices(catalog: dict, ids, limit: int | None = None) -> list:
    """Label known tracks in the order supplied."""
    tracks = catalog["tracks"]
    result = []
    for identifier in map(str, ids):
        if identifier in tracks:
            result.append({"id": identifier, "name": tracks[identifier]["title"]})
            if limit and len(result) == limit:
                break
    return result


def browse(catalog: dict, params: dict) -> list:
    """Filter and rank the library for picker views."""
    mode = params.get("mode", [""])[0]
    value = params.get("value", [""])[0]
    tracks = catalog["tracks"]
    if mode in ("genre", "year"):
        labels = {str(item.get(mode) or "") for item in tracks.values()} - {""}
        if not value:
            return [{"id": label, "name": label}
                    for label in sorted(labels, key=str.casefold)]
        ids = [key for key, item in tracks.items()
               if str(item.get(mode)) == value]
        return track_choices(catalog, ids)
    if mode in ("tag", "playlist"):
        items = catalog["tags" if mode == "tag" else "playlists"]
        if value:
            return track_choices(catalog, items[value].get("tracks", []))
        return sorted(
            ({"id": key, "name": item["name"]} for key, item in items.items()),
            key=lambda item: item["name"].casefold(),
        )
    if mode == "search" and 0 < len(value) <= 100:
        needle = value.casefold()
        matches = []
        for kind, field in (("tracks", "title"), ("artists", "name"),
                            ("albums", "name")):
            results = [
                {"id": kind + ":" + key, "name": item[field] + " (" + kind[:-1] + ")"}
                for key, item in catalog[kind].items()
                if needle in item[field].casefold()
            ]
            matches.extend(sorted(results, key=lambda item: item["name"].casefold())[:50])
        return matches
    if mode in ("recent", "rated", "played"):
        field = {"recent": "uploaded_on", "rated": "rating", "played": "plays"}[mode]
        if mode == "recent":
            sort_key = lambda key: (tracks[key].get(field) or "",
                                    tracks[key].get("uploaded_time") or "")
        else:
            sort_key = lambda key: int(tracks[key].get(field) or 0)
        ids = sorted((key for key, item in tracks.items() if item.get(field)),
                     key=sort_key, reverse=True)
        return track_choices(catalog, ids, 100)
    if mode == "random":
        count = int(value)
        if 1 <= count <= 100:
            return track_choices(catalog, random.sample(list(tracks),
                                 min(count, len(tracks))))
    raise ValueError("Invalid library view")


class Handler(BaseHTTPRequestHandler):
    """Serve library choices and proxy audio to MPD."""

    def do_GET(self) -> None:
        """Route requests by resource and numeric ID."""
        target = urlsplit(self.path)
        if target.path == "/browse":
            try:
                library = self.server.library
                with library.lock:
                    choices = browse(library.snapshot(), parse_qs(target.query))
                self.send_page(choices, parse_qs(target.query))
            except (KeyError, OSError, TypeError, ValueError):
                self.send_error(502, "Could not browse iBroadcast")
            return
        route = re.fullmatch(
            r"/(artists|albums|tracks|stream)(?:/([0-9]+))?", target.path
        )
        if not route or (route[1] in ("tracks", "stream") and not route[2]):
            self.send_error(404)
            return
        library = self.server.library
        try:
            with library.lock:
                catalog = library.snapshot()
                if route[1] == "stream":
                    track = catalog["tracks"][route[2]]
                    album = catalog["albums"].get(str(track.get("album_id")), {})
                    artwork = (album.get("artwork_id"), track.get("artwork_id"))
                    url = library.stream_url(route[2], track)
                else:
                    self.send_choices(catalog, route[1], route[2], parse_qs(target.query))
                    return
            origin = os.environ.get("MYMPD_STREAM_ORIGIN", "http://127.0.0.1:18769")
            cover_uri = origin.rstrip("/") + self.path
            for artwork_id in dict.fromkeys(map(str, artwork)):
                if re.fullmatch(r"[1-9][0-9]*", artwork_id) and cache_cover(
                    cover_uri, f"https://artwork.ibroadcast.com/artwork/{artwork_id}-1000"
                ):
                    break
            request = Request(url)
            byte_range = self.headers.get("Range", "")
            if re.fullmatch(r"bytes=[0-9]+-[0-9]*", byte_range):
                request.add_header("Range", byte_range)
            response = urlopen(request, timeout=15)
        except (KeyError, OSError, ValueError, TypeError):
            self.send_error(502, "Could not load iBroadcast media or library")
            return

        with response:
            self.send_response(response.status)
            for header in ("Content-Type", "Content-Length", "Content-Range",
                           "Accept-Ranges"):
                if value := response.headers.get(header):
                    self.send_header(header, value)
            self.end_headers()
            try:
                shutil.copyfileobj(response, self.wfile, length=65536)
            except OSError:
                self.close_connection = True

    def send_choices(self, catalog: dict, kind: str, parent: str | None,
                     params: dict) -> None:
        """Return display labels and IDs in browse or album order."""
        rows = catalog[kind]
        if kind == "tracks":
            ids = catalog["albums"][parent]["tracks"]
            choices = [{"id": str(key), "name": rows[str(key)]["title"]}
                       for key in ids if str(key) in rows]
        else:
            artist_albums = set()
            if kind == "albums" and parent is not None:
                artist = catalog["artists"].get(parent, {})
                for track_id in artist.get("tracks", []):
                    track = catalog["tracks"].get(str(track_id))
                    if track:
                        artist_albums.add(str(track.get("album_id")))
            choices = [
                {"id": key, "name": item["name"]}
                for key, item in rows.items()
                if parent is None or str(item.get("artist_id")) == parent
                or key in artist_albums
            ]
            choices.sort(key=lambda item: item["name"].casefold())
        self.send_page(choices, params)

    def send_page(self, choices: list, params: dict) -> None:
        """Keep picker responses small while exposing every choice."""
        if "offset" not in params:
            self.send_json(choices)
            return
        offset = int(params["offset"][0])
        if offset < 0:
            raise ValueError("Invalid picker offset")
        page_size = 75
        page = {"entries": choices[offset:offset + page_size],
                "total": len(choices)}
        if offset:
            page["previous"] = max(0, offset - page_size)
        if offset + page_size < len(choices):
            page["next"] = offset + page_size
        self.send_json(page)

    def do_POST(self) -> None:
        """Save the supplied track order to a private iBroadcast playlist."""
        if self.path != "/playlists":
            self.send_error(404)
            return
        if self.headers.get("Origin"):
            self.send_error(403)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if self.headers.get_content_type() != "application/json" or not 0 < size <= 8192:
                raise ValueError("Invalid request body")
            data = json.loads(self.rfile.read(size))
            name, ids = data["name"], data["ids"]
            if (not isinstance(name, str) or not name.strip()
                    or len(name) > 100 or any(ord(char) < 32 for char in name)
                    or not isinstance(ids, list) or not 1 <= len(ids) <= 100
                    or any(not isinstance(key, str) for key in ids)
                    or len(set(ids)) != len(ids)):
                raise ValueError("Invalid playlist")
        except (KeyError, TypeError, ValueError):
            self.send_error(400, "Invalid playlist")
            return
        library = self.server.library
        try:
            with library.lock:
                if any(key not in library.snapshot()["tracks"] for key in ids):
                    self.send_error(400, "Unknown track")
                    return
                library.api(
                    "https://api.ibroadcast.com/createplaylist", "createplaylist",
                    name=name.strip(), tracks=[int(key) for key in ids],
                    make_public=False,
                )
                library.updated = 0
        except (KeyError, OSError, TypeError, ValueError):
            self.send_error(502, "Could not save playlist to iBroadcast")
            return
        self.send_json({"saved": True})

    def send_json(self, data) -> None:
        """Return a JSON response."""
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    """Authorize an account or serve its library."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("authorize", "serve"))
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--write-playlists", action="store_true",
                        help="Request permission to save iBroadcast playlists")
    parser.add_argument("--listen", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18769)
    args = parser.parse_args()
    client_id = os.environ.get("IBROADCAST_CLIENT_ID")
    if not client_id:
        parser.error("Set IBROADCAST_CLIENT_ID to your registered app ID")
    try:
        if args.operation == "authorize":
            authorize(client_id, args.token_file, args.write_playlists)
        else:
            server = ThreadingHTTPServer((args.listen, args.port), Handler)
            server.library = Library(client_id, args.token_file)
            with server:
                server.serve_forever()
    except (KeyError, OSError, TypeError, ValueError):
        parser.exit(1, "iBroadcast setup failed. Check the app ID and token file.\n")


if __name__ == "__main__":
    main()
