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

GitHub Actions publishes the bridge image to GitHub Container Registry.
Pull it with:

```bash
docker pull ghcr.io/bonelifer/mympd-ibroadcast:latest
```

Use the published image with the same environment, volumes, and command as
the local Compose build by setting `image: ghcr.io/bonelifer/mympd-ibroadcast:latest`
in place of `build: .`.


Register an app in the iBroadcast web player's developer settings.
Copy `.env.example` to `.env` and enter the app ID. Authorize the bridge
with your iBroadcast account, then start it:

```bash
mkdir -m 700 -p state
docker compose build
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

Enable Lua scripting in myMPD and install the picker:

```bash
python3 install-picker.py --mympd http://127.0.0.1:8081 --workdir /path/to/mympd/workdir
```

For an existing installation, run the installer with `--replace`.

The installer copies the bundled Home icon to `pics/thumbs/` and creates
or updates the matching tile. Omit `--workdir` to install only the Lua script.
If API protection is on, pass your session as `MYMPD_SESSION`.

For the Playback page button, copy `assets/ibroadcast.png` and
`ibroadcast-playback.js` to myMPD's `pics/` directory. Append the contents of
`ibroadcast-loader.js` to `config/custom_js` once. Restart myMPD and reload
the browser.

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
