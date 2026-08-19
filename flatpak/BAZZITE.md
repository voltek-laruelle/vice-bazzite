# Vice on Bazzite — Flatpak install

## Why this exists

`install.sh` refuses to run on Bazzite/Silverblue/Kinoite/any rpm-ostree
system on purpose — it shells out to `dnf`, and layering packages on an
atomic distro is not something to do casually. This Flatpak is the
atomic-safe path the README points to (issue #97).

## What is, and isn't, sandboxed

Vice needs a few things a Flatpak sandbox fundamentally can't provide on
its own:

- **Global hotkeys (evdev)** — needs raw `/dev/input/event*` access.
  Solved with `--device=all` in the manifest.
- **gpu-screen-recorder** — its KMS capture path relies on a small helper
  installed with `cap_sys_admin` at the system level. That cannot exist
  inside a Flatpak sandbox. Solved by keeping GSR installed on the *host*
  and calling it through `flatpak-spawn --host` (wrapper scripts in
  `hostexec/`).
- **xdotool / xprop / wmctrl** (game-window detection for Discord presence
  and clip tagging) and **cloudflared** (public share links) — same
  treatment, same reason.
- **systemd user service** — a service *inside* the sandbox isn't a thing.
  Keep the unit on the host; it just calls `flatpak run`. See below.

Everything else (the daemon, the web UI, clip storage, config) runs
normally inside the sandbox.

## 1. Install host-side dependencies (once)

Bazzite ships most of gaming-related tooling already, but not
gpu-screen-recorder or the window-detection tools. Layer them with
`rpm-ostree`, then reboot:

```bash
rpm-ostree install gpu-screen-recorder xdotool xorg-x11-utils wmctrl
systemctl reboot
```

If `gpu-screen-recorder` isn't in the enabled repos on your Bazzite image,
grab it from its COPR/repo per https://git.dec05eba.com/gpu-screen-recorder/
— it installs a udev/setcap step that genuinely needs to happen on the
host, so this one can't be worked around from inside Flatpak no matter
what.

`cloudflared` is optional (only used for public share links off your LAN);
install the same way if you want it, or skip it — Vice degrades to
LAN-only share links without it (this matches upstream behavior).

## 2. Build the Flatpak (on a machine with internet)

This repo layout expected:

```
Vice/                              <- project root (has pyproject.toml)
└── flatpak/
    ├── io.github.eklonofficial.Vice.yml
    ├── io.github.eklonofficial.Vice.desktop
    ├── io.github.eklonofficial.Vice.metainfo.xml
    ├── vice-flatpak-launcher
    └── hostexec/...
```

Drop the `flatpak/` folder (everything in this bundle) into the root of
your `Vice` checkout, then:

```bash
flatpak install -y flathub org.freedesktop.Platform//23.08 org.freedesktop.Sdk//23.08
cd Vice/flatpak
flatpak-builder --user --install --force-clean build-dir \
  io.github.eklonofficial.Vice.yml
```

The `vice` module's build step needs network access (it `pip install`s
evdev/aiohttp/click/tomli-w/psutil from PyPI) — that's why it has its own
`--share=network` build-arg scoped to just that module. Nothing in the
*installed* app gets network access unless you grant it yourself.

## 3. Grant the runtime permissions

`flatpak-builder --install` applies `finish-args` from the manifest
automatically. If you ever need to re-apply or tweak them by hand:

```bash
flatpak override --user io.github.eklonofficial.Vice \
  --device=all --device=dri \
  --socket=wayland --socket=fallback-x11 --socket=pulseaudio \
  --talk-name=org.freedesktop.Flatpak \
  --filesystem=xdg-run/discord-ipc-0 \
  --filesystem=xdg-config/vice:create \
  --filesystem=xdg-videos:create \
  --filesystem=xdg-data/vice:create
```

## 4. Add yourself to `input`, or rely on uaccess

Vice's own udev rule (`packaging/vice.rules`, tagging input devices with
`uaccess`) needs to be installed on the **host**, since that's a kernel/udev
level thing, not something the Flatpak sandbox can grant on its own:

```bash
sudo install -Dm644 packaging/vice.rules /etc/udev/rules.d/70-vice-input.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
```

(On most desktop sessions, `uaccess` already gives the locally logged-in
user read access to `/dev/input/event*` without this, but it's cheap
insurance and matches what the AUR package does.)

## 5. Run it

```bash
flatpak run io.github.eklonofficial.Vice          # opens the app / browser UI
flatpak run io.github.eklonofficial.Vice status
flatpak run io.github.eklonofficial.Vice doctor
```

## 6. Optional: start-at-login, the atomic-safe way

Don't try to run `systemctl --user enable --now vice.service` *inside* the
sandbox — put the unit on the host and have it call `flatpak run`:

```ini
# ~/.config/systemd/user/vice.service
[Unit]
Description=Vice game clip recorder daemon (flatpak)
After=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/flatpak run io.github.eklonofficial.Vice start --no-open-ui
Restart=on-failure
RestartSec=3

[Install]
WantedBy=graphical-session.target default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now vice.service
```

## Known rough edges

- **No in-app clip playback via pywebview.** The manifest deliberately
  skips `pywebview` (and therefore WebKit/Qt entirely) — `vice-app` already
  falls back to opening the web UI in your default browser when pywebview
  isn't installed, which sidesteps the whole "PyPI WebEngine wheel has no
  H.264 decoder" problem `install.sh` works around on non-Flatpak installs.
  Clips still play fine in your browser or system video player.
- **`--device=all` is broad.** It's the least-bad option Flatpak currently
  offers for raw evdev access; there's no finer-grained "just /dev/input"
  permission today.
- Not submitted to Flathub — this manifest assumes host tool availability
  that Flathub's build isolation wouldn't allow, so it's meant for local/
  personal builds like this one, not store distribution.
