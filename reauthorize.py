#!/usr/bin/env python3
"""Renew a rejected iBroadcast login through the owner's running Firefox.

The bridge's /health endpoint reports "rejected" once iBroadcast refuses
the saved refresh token. This script then starts the same OAuth device
flow as `bridge.py authorize`, opens the sign-in link in Firefox through
the local Open Browser Control bridge, clicks Continue and Allow for its
own code only, and saves the new token where the bridge reads it. The
bridge picks the new file up on its next request.

No password is stored: it relies on Firefox already being signed in to
iBroadcast. Anything unexpected (Firefox not running, signed out, a page
it does not recognize, the code expiring) stops the attempt and sends one
Gotify alert per outage. It never retries within a run.

Operations:
  check        Ask the bridge for its status; renew only when rejected.
               Meant for the systemd timer.
  reauthorize  Renew now, whatever the bridge reports.

Settings come from ~/.config/mympd-ibroadcast/reauthorize.ini, seeded
from reauthorize.ini.example next to this script on first run.
"""

import argparse
import configparser
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
CONFIG_DIR = Path.home() / ".config" / "mympd-ibroadcast"
CONFIG_FILE = CONFIG_DIR / "reauthorize.ini"
STATE_DIR = Path.home() / ".local" / "state" / "mympd-ibroadcast"
VERBOSE = False
VENV = Path.home() / ".local" / "share" / "mympd-ibroadcast" / "venv"
REQUIREMENTS = ["websockets>=13,<18"]


def ensure_venv() -> None:
    """Re-run this script inside its own virtual environment.

    Creates the environment and installs the websockets package on first
    use, so no manual setup is needed.
    """
    if Path(sys.prefix).resolve() == VENV.resolve():
        return
    python = VENV / "bin" / "python3"
    if not python.exists():
        print("Setting up the Python environment (first run only)...",
              file=sys.stderr)
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
        subprocess.run([str(python), "-m", "pip", "install", "--quiet",
                        "--disable-pip-version-check",
                        *REQUIREMENTS], check=True)
    os.execv(str(python), [str(python), str(Path(__file__).resolve()),
                           *sys.argv[1:]])


ensure_venv()

from websockets.sync.client import connect  # noqa: E402

sys.path.insert(0, str(HERE))
from bridge import OAUTH, save_token, token_request  # noqa: E402


class Stop(Exception):
    """An attempt that cannot continue without the owner."""


def load_config(path: Path) -> configparser.SectionProxy:
    """Read settings, seeding the default file from the example once."""
    if not path.exists() and path == CONFIG_FILE:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(HERE / "reauthorize.ini.example", CONFIG_FILE)
        CONFIG_FILE.chmod(0o600)
        print(f"Created {CONFIG_FILE}; fill in gotify_token there.",
              file=sys.stderr)
    parser = configparser.ConfigParser()
    parser.read_string("[reauthorize]\n" + path.read_text(encoding="utf-8"))
    section = parser["reauthorize"]
    for key in ("bridge_url", "token_file", "env_file", "firefox_bridge",
                "gotify_url"):
        if not section.get(key, "").strip():
            raise SystemExit(f"{path}: set {key}")
    return section


def client_id(config: configparser.SectionProxy) -> str:
    """Read IBROADCAST_CLIENT_ID from the bridge's .env file."""
    env_file = Path(config["env_file"]).expanduser()
    for line in env_file.read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        if name.strip() == "IBROADCAST_CLIENT_ID" and value.strip():
            return value.strip().strip("'\"")
    raise SystemExit(f"{env_file}: IBROADCAST_CLIENT_ID is not set")


class Notifier:
    """Send Gotify messages, at most one failure alert per outage."""

    def __init__(self, config: configparser.SectionProxy):
        self.url = config["gotify_url"].rstrip("/") + "/message"
        self.token = config.get("gotify_token", "").strip()
        self.priority = config.getint("gotify_priority", 5)
        self.state_file = STATE_DIR / "reauthorize.json"
        try:
            self.state = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.state = {}

    def save(self) -> None:
        """Write alert state atomically."""
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        temporary = self.state_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state), encoding="utf-8")
        temporary.replace(self.state_file)

    def send(self, title: str, message: str, priority: int) -> None:
        """Post one message; report delivery failures without raising."""
        if not self.token:
            print(f"No gotify_token set; not sent: {title}: {message}",
                  file=sys.stderr)
            return
        body = json.dumps({"title": title, "message": message,
                           "priority": priority}).encode()
        request = Request(self.url, body, {"Content-Type": "application/json",
                                           "X-Gotify-Key": self.token})
        try:
            with urlopen(request, timeout=10):
                pass
        except (OSError, ValueError) as error:
            print(f"Could not send Gotify alert: {error}", file=sys.stderr)

    def failed(self, reason: str) -> None:
        """Alert once until the login works again."""
        print(f"Renewal stopped: {reason}", file=sys.stderr)
        if self.state.get("alerted"):
            return
        self.send("iBroadcast login needs attention",
                  f"{reason}\n\nTo renew by hand: stop the bridge, run "
                  "`docker compose run --rm ibroadcast authorize --token-file "
                  "/state/token.json --write-playlists`, then start it.",
                  self.priority)
        self.state = {"alerted": True, "reason": reason}

    def attempted(self) -> None:
        """Remember when renewal last ran, for the retry back-off."""
        self.state["last_attempt"] = time.time()
        self.save()

    def backing_off(self, minutes: int) -> bool:
        """Whether a failed attempt was too recent to try again.

        Keeps a signed-out Firefox from getting a new tab every check.
        """
        last = self.state.get("last_attempt", 0)
        return bool(self.state.get("alerted")) and time.time() - last < minutes * 60

    def healthy(self, renewed: bool) -> None:
        """Clear the outage, telling the owner only if it was reported."""
        if renewed:
            self.send("iBroadcast login renewed",
                      "The bridge's iBroadcast login was renewed through Firefox.",
                      max(1, self.priority - 3))
        elif self.state.get("alerted"):
            self.send("iBroadcast login works again",
                      "The bridge's iBroadcast login is working.",
                      max(1, self.priority - 3))
        if self.state:
            self.state = {}
            self.save()


def bridge_health(config: configparser.SectionProxy) -> dict:
    """Return the bridge's /health report."""
    url = config["bridge_url"].rstrip("/") + "/health"
    with urlopen(url, timeout=30) as response:
        return json.load(response)


class Firefox:
    """Drive one tab of the running Firefox through Open Browser Control."""

    def __init__(self, url: str):
        self.url = url
        self.counter = 0

    def __enter__(self) -> "Firefox":
        """Connect to the bridge and start a named session."""
        try:
            self.connection = connect(self.url, open_timeout=5)
            self.socket = self.connection.__enter__()
        except (OSError, TimeoutError) as error:
            raise Stop("The Firefox automation bridge is not running "
                       f"({error}). Start Firefox and try again.") from error
        self.socket.send(json.dumps({"type": "session_start",
                                     "name": "iBroadcast re-login"}))
        self.wait_for(lambda message: message.get("type") == "session_started")
        return self

    def __exit__(self, *details) -> None:
        """Close the bridge connection."""
        self.connection.__exit__(*details)

    def wait_for(self, matches, timeout: float = 15) -> dict:
        """Return the first bridge message that matches."""
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                message = json.loads(self.socket.recv(timeout=remaining))
            except TimeoutError:
                break
            if message.get("type") == "event" and message.get("event") == "rejected":
                raise Stop("The Firefox extension refused the connection.")
            if matches(message):
                return message
        raise Stop("Firefox did not answer. Is the Open Browser Control "
                   "extension connected?")

    def action(self, name: str, **params) -> dict:
        """Run one extension action and return its data."""
        self.counter += 1
        identifier = f"reauthorize-{self.counter}"
        self.socket.send(json.dumps({"type": "action", "action": name,
                                     "id": identifier, "params": params}))
        result = self.wait_for(lambda message: message.get("type") == "result"
                               and message.get("id") == identifier, 30)
        if not result.get("success"):
            raise Stop(f"Firefox could not {name}: {result.get('error')}")
        return result.get("data") or {}

    def evaluate(self, expression: str):
        """Evaluate JavaScript in the session's tab."""
        return self.action("execute_js", expression=expression).get("result")

    def loaded_url(self) -> str | None:
        """Return the tab's address once a web page has finished loading.

        A new tab starts blank, where Firefox allows no page access, so
        errors while it is loading just mean "not yet".
        """
        try:
            info = self.action("get_page_info").get("pageInfo") or {}
        except Stop:
            return None
        if info.get("readyState") != "complete":
            return None
        url = info.get("url", "")
        return url if url.startswith("https://") else None


# Reads the device page without changing it. Returns JSON describing the
# step: the address, the heading, whether an Allow-style button exists,
# and whether a password field (signed out) is showing.
PAGE_STATE = """JSON.stringify({
  url: location.href,
  heading: (document.querySelector('h1,h2,h3,h4') || {}).textContent || '',
  allow: !!document.querySelector('a.oauth-button-allow'),
  password: !!document.querySelector('input[type=password]'),
  text: document.body ? document.body.innerText.slice(0, 300) : ''
})"""


def approve(firefox: Firefox, link: str, poll, deadline: float) -> dict:
    """Click through Continue and Allow for this code, polling for a token.

    Only clicks on oauth.ibroadcast.com pages whose URL carries this run's
    own user_code, so it never approves another request.
    """
    # iBroadcast formats the code differently in its JSON, so compare
    # against the code in the link this run opened.
    user_code = parse_qs(urlsplit(link).query).get("user_code")
    if not user_code:
        raise Stop("iBroadcast's sign-in link has no code in it.")
    firefox.action("new_tab", url=link)
    clicked = set()
    while time.monotonic() < deadline:
        token = poll()
        if token:
            return token
        if not firefox.loaded_url():
            time.sleep(1)
            continue
        state = json.loads(firefox.evaluate(PAGE_STATE) or "{}")
        address = urlsplit(state.get("url", ""))
        own_page = (address.scheme == "https"
                    and address.netloc == "oauth.ibroadcast.com"
                    and parse_qs(address.query).get("user_code") == user_code)
        if state.get("password"):
            raise Stop("Firefox is not signed in to iBroadcast. Sign in at "
                       "https://media.ibroadcast.com in Firefox.")
        step = state.get("heading", "").strip()
        if VERBOSE:
            print(f"page: {address.netloc}{address.path} own_code={own_page} "
                  f"heading={step!r} allow={state.get('allow')}", file=sys.stderr)
        if own_page and state.get("allow") and step not in clicked:
            if len(clicked) >= 2:
                raise Stop(f"iBroadcast showed an unexpected extra step: {step!r}")
            clicked.add(step)
            # A real click: the page ignores script-generated .click().
            firefox.action("click", selector="a.oauth-button-allow")
        elif (address.scheme == "https" and address.netloc != "ibroadcast.com"
              and not address.netloc.endswith(".ibroadcast.com")):
            # about:blank while loading and iBroadcast's own pages just wait.
            raise Stop("iBroadcast's sign-in went somewhere unexpected "
                       f"({address.netloc}{address.path}). Check Firefox.")
        time.sleep(2)
    raise Stop("The sign-in code expired before iBroadcast approved it.")


def reauthorize(config: configparser.SectionProxy) -> None:
    """Run the device flow through Firefox and save the new token."""
    identifier = client_id(config)
    scope = "user.account:read user.library:read"
    if config.getboolean("write_playlists", True):
        scope += " user.library:write"
    query = urlencode({"client_id": identifier, "scope": scope})
    with urlopen(f"{OAUTH}/device/code?{query}", timeout=15) as response:
        device = json.load(response)
    link = device.get("verification_uri_complete", "")
    if not link.startswith("https://oauth.ibroadcast.com/"):
        raise Stop("iBroadcast returned an unexpected sign-in link.")
    deadline = time.monotonic() + int(device["expires_in"]) - 5
    interval = max(1, int(device["interval"]))
    last_poll = [0.0]

    def poll() -> dict | None:
        """Ask once for the token if the polling interval has passed."""
        nonlocal interval
        if time.monotonic() - last_poll[0] < interval:
            return None
        last_poll[0] = time.monotonic()
        token = token_request(identifier, "device_code",
                              device_code=device["device_code"])
        error = token.get("error")
        if error == "slow_down":
            interval += 5
        elif error and error != "authorization_pending":
            raise Stop(f"iBroadcast refused the sign-in ({error}).")
        return None if error else token

    with Firefox(config["firefox_bridge"]) as firefox:
        token = approve(firefox, link, poll, deadline)
    path = Path(config["token_file"]).expanduser()
    save_token(path, token)
    path.chmod(0o600)
    print(f"Saved the new iBroadcast token to {path}")


def main() -> None:
    """Parse arguments and run one check or renewal."""
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        epilog="Settings: " + str(CONFIG_FILE))
    parser.add_argument("operation", nargs="?", choices=("check", "reauthorize"),
                        help="check: renew only if the bridge reports a "
                        "rejected login; reauthorize: renew now")
    parser.add_argument("-c", "--config", type=Path, default=CONFIG_FILE,
                        help=f"settings file (default: {CONFIG_FILE})")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print each sign-in page step while renewing")
    args = parser.parse_args()
    global VERBOSE
    VERBOSE = args.verbose
    if not args.operation:
        parser.print_help()
        return
    config = load_config(args.config.expanduser())
    notifier = Notifier(config)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / "reauthorize.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another renewal is already running.", file=sys.stderr)
            return
        if args.operation == "check":
            try:
                health = bridge_health(config)
            except (OSError, ValueError) as error:
                print(f"Bridge not reachable ({error}); nothing to do.",
                      file=sys.stderr)
                return
            if health.get("auth") == "ok":
                notifier.healthy(renewed=False)
                return
            if health.get("auth") != "rejected":
                print(f"Bridge status unclear: {health}", file=sys.stderr)
                return
            if notifier.backing_off(config.getint("retry_minutes", 120)):
                print("Last renewal failed recently; waiting before retrying.",
                      file=sys.stderr)
                return
        try:
            reauthorize(config)
        except Stop as error:
            notifier.failed(str(error))
            notifier.attempted()
            sys.exit(1)
        except (KeyError, OSError, URLError, ValueError) as error:
            notifier.failed(f"Renewal failed: {type(error).__name__}: {error}")
            notifier.attempted()
            sys.exit(1)
        notifier.healthy(renewed=True)


if __name__ == "__main__":
    main()
