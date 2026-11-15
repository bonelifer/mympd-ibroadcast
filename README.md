# iBroadcast for myMPD

Open iBroadcast from Home, Playback, or the Scripts menu. Browse your library
by artist, album, playlist, genre, year, or tag. Search for tracks, artists,
and albums, or browse recently uploaded, top rated, and most played tracks.
Play a single random song immediately, or append up to ten distinct random
songs without interrupting playback.

Create a named random playlist with up to 100 songs. Choose MPD, iBroadcast,
or both. Both destinations receive the same tracks in the same order. MPD
playlists need the bridge running when played. The bridge keeps iBroadcast
credentials on the server.

## Setup

GitHub Actions builds the bridge image on every push to `main` and
publishes it to GitHub Container Registry as
`ghcr.io/bonelifer/mympd-ibroadcast:latest`. `compose.yaml` uses that image.
Update a running bridge with `docker compose pull && docker compose up -d`.
To run your own changes instead, build the same tag locally with
`docker build -t ghcr.io/bonelifer/mympd-ibroadcast:latest .` first.

Register an app in the iBroadcast web player's developer settings.
Copy `.env.example` to `.env` and enter the app ID. Authorize the bridge
with your iBroadcast account, then start it:

```bash
mkdir -m 700 -p state
docker compose pull
docker compose run --rm ibroadcast authorize --token-file /state/token.json --write-playlists
docker compose up -d
```

Omit `--write-playlists` for read-only iBroadcast access, including when
saving playlists only to MPD. If already authorized with read-only access,
stop the bridge, authorize again with `--write-playlists`, and restart it.
If a save to both destinations only confirms MPD, check iBroadcast before
retrying to avoid creating a duplicate.

To run without Docker, export `IBROADCAST_CLIENT_ID` and run
`python3 bridge.py authorize --token-file state/token.json --write-playlists` followed by
`python3 bridge.py serve --token-file state/token.json`.

Keep the bridge on loopback. The default listener is `127.0.0.1:18769`,
for myMPD and MPD using host networking. Any local process that can reach
the listener can browse and play the connected library. Stop the bridge
before reauthorizing the account.

If every request fails with a 502, check `docker compose logs ibroadcast`.
The bridge logs the underlying error there. A refresh token rejected with
`invalid_grant` means iBroadcast no longer accepts the saved credentials:
stop the bridge, run the `authorize` command above again, and start it.

Enable Lua scripting in myMPD and install the picker:

```bash
python3 install-picker.py --mympd http://127.0.0.1:8081 --workdir /path/to/mympd/workdir
```

For an existing installation, run the installer with `--replace`.

The installer copies the bundled Home icon to `pics/thumbs/` and creates
or updates the matching tile. Omit `--workdir` to install only the Lua script.
If API protection is on, pass your session as `MYMPD_SESSION`.

For the Playback page button, copy `ibroadcast-playback.js` to myMPD's
`pics/` directory. The button uses the Home tile icon in `pics/thumbs/`.
Append the contents of
`ibroadcast-loader.js` to `config/custom_js` once. Restart myMPD and reload
the browser.

## Automatic login renewal

The bridge renews its hourly access token on its own. If iBroadcast stops
accepting the saved login altogether, `reauthorize.py` can sign it in again
through your running Firefox, with no password stored. It needs Firefox
signed in to iBroadcast and the Open Browser Control extension connected to
its local bridge. It opens the sign-in link, clicks Continue and Allow for
its own code only, and saves the new token where the bridge reads it. The
bridge uses the new token on its next request, without a restart.

Run `./reauthorize.py` for its options. The first run sets up its own Python
environment and creates `~/.config/mympd-ibroadcast/reauthorize.ini` from
`reauthorize.ini.example`; add a Gotify app token there for alerts. To check
every 15 minutes, install the user timer:

```bash
cp systemd/mympd-ibroadcast-reauthorize.* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now mympd-ibroadcast-reauthorize.timer
```

Each check asks the bridge's `/health` endpoint and renews only when the
login was rejected. If renewal cannot finish (Firefox closed, signed out of
iBroadcast, an unfamiliar page, or the code expiring), it sends one Gotify
alert per outage and tries again after `retry_minutes` (two hours by
default).

## Playback artwork

Set `MYMPD_THUMBS_DIR` in Compose's `.env` to the absolute path of myMPD's
`pics/thumbs/` directory. The bridge writes covers there for myMPD's native
Playback and footer artwork. If you installed the picker with a custom
bridge URL, set `MYMPD_STREAM_ORIGIN` to that same URL. Restart the bridge after changing the path.

## Contributing

Contributions are welcome!

- **Bug reports**: [Open an issue](https://github.com/bonelifer/mympd-ibroadcast/issues).
- **Everything else** (questions, feature requests, ideas, general discussion): [Use Discussions](https://github.com/bonelifer/mympd-ibroadcast/discussions).
- Pull requests are welcome for bug fixes or discussed features.

## Acknowledgments

- Code, testing, and documentation assisted by [Codex](https://openai.com/codex/).

## License

Code is licensed under GPL-3.0. The original icon is [CC0](assets/LICENSE.md).
