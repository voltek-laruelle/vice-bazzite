<p align="center">
  <img src="assets/vice.svg" width="96" alt="Vice icon"/>
</p>

<h1 align="center">Vice for Bazzite</h1>

<p align="center">
  <b>Instant-replay game clipping for Linux, packaged as a Flatpak for Bazzite.</b><br/>
  Press one key to save the last 20 seconds of gameplay. No scenes, no setup, no upload.
</p>

<p align="center">
  <a href="#install">Install</a> ·
  <a href="#launch">Launch</a> ·
  <a href="#features">Features</a> ·
  <a href="#configuration">Config</a> ·
  <a href="#troubleshooting">Troubleshooting</a>
</p>

<table align="center">
  <tr>
    <td align="center" width="50%">
      <img src="assets/screenshots/home.png" width="420"/><br/>
      <sub>Home: recent clips, playlists, and quick toggles</sub>
    </td>
    <td align="center" width="50%">
      <img src="assets/screenshots/editor.png" width="420"/><br/>
      <sub>Editor: multi-track timeline with clips, text, and audio</sub>
    </td>
  </tr>
  <tr>
    <td align="center" width="50%">
      <img src="assets/screenshots/viewer.png" width="420"/><br/>
      <sub>Viewer: highlight markers dropped mid-session</sub>
    </td>
    <td align="center" width="50%">
      <img src="assets/screenshots/trim.png" width="420"/><br/>
      <sub>Trim a clip in place, no re-encode needed</sub>
    </td>
  </tr>
</table>

---

## Why this fork

Upstream Vice's `install.sh` refuses to run on Bazzite (and any other rpm-ostree/atomic
Fedora system) on purpose, since layering packages with `dnf`/`apt` isn't safe on an
immutable base. This fork packages Vice as a **Flatpak**, with a one-shot installer
that handles everything else Bazzite needs around it: `gpu-screen-recorder` (also a
Flatpak, bridged in via `flatpak-spawn`), the host tools it shells out to
(`xdotool`, `wmctrl`, `ffmpeg`), the udev rule for global hotkeys, and a couple of
Fedora-specific codec quirks.

## Install

```bash
git clone https://github.com/editeurlaruelle-cmd/vice-bazzite.git
cd vice-bazzite
./install-bazzite.sh
```

The installer is **idempotent** and safe to re-run: it checks what's already in
place before touching anything, and only asks `rpm-ostree` to install what's
actually missing. If it needs to layer a system package, it stops and tells you to
reboot, then pick up where it left off:

```bash
systemctl reboot
# after logging back in:
cd vice-bazzite && ./install-bazzite.sh
```

Re-run it as many times as you need; each already-completed step is skipped.

What it sets up, in order:

1. Detects and offers to clean up a previous non-Flatpak Vice install
2. Layers missing host packages via `rpm-ostree` (`flatpak-builder`, `xdotool`,
   `xorg-x11-utils`, `wmctrl`, `wf-recorder`) and swaps `ffmpeg-free` → `ffmpeg`
   if the system doesn't already have a working H.264 decoder
3. Installs the udev rule so hotkeys can read `/dev/input` without `sudo`
4. Installs `gpu-screen-recorder` from Flathub, system-wide
5. Installs the `org.freedesktop.Platform`/`Sdk` 23.08 runtimes
6. Patches `vice/recorder.py` so the clip-save signal reaches
   `gpu-screen-recorder` correctly through the nested Flatpak sandbox
7. Builds and installs the Vice Flatpak itself

See [`flatpak/BAZZITE.md`](flatpak/BAZZITE.md) for what each of these actually does
and why it's needed, if you want the long version.

## Launch

```bash
flatpak run io.github.eklonofficial.Vice
```

This opens Vice's web UI in your default browser (the Flatpak build skips the
embedded native window on purpose — see [Troubleshooting](#troubleshooting)).
Press **F9** in a game to save a clip.

Check everything is wired up correctly at any time with:

```bash
flatpak run io.github.eklonofficial.Vice doctor
```

### Start at login

Flatpak sandboxes can't run their own systemd user service, so the unit lives on
the host and just calls `flatpak run`:

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/vice.service <<'EOF'
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
EOF

systemctl --user daemon-reload
systemctl --user enable --now vice.service
```

## Features

**Free and open source.** No account, no subscription, no telemetry. Read the code, change it, ship your own build.

**Share links that just work.** Every clip gets a public URL the moment you save it, no upload step, no size limit. Paste it into Discord and it plays inline as an embed, tinted to match your theme color.

**A real timeline editor, built in.** Multiple video and audio tracks, transitions, and text overlays, all free and included. Trim, arrange, and layer clips into something bigger without leaving Vice.

**Playlists.** Clips file themselves into a playlist for whatever game you were playing, automatically. Make your own for anything else, and drag clips between them.

**Driver-level capture.** Vice runs on `gpu-screen-recorder`, the same approach ShadowPlay uses: it talks to NVENC/VAAPI directly instead of compositing a scene. Typical CPU usage is under 1%.

**Discord Rich Presence.** Shows what game you're clipping right in your Discord status, on by default for known games.

**Clips titled for you.** Vice recognizes the game you're playing from a curated list and names the file accordingly, no guessing from window titles.

**Hotkeys per clip.** Add your own rename-able, color-coded hotkeys to any clip for quick recall later.

**Vice Sessions.** Double-tap your clip key to start recording a full match. Single-tap during the session to drop a marker at that moment, then pick up right where you left off once it lands in the editor.

**Tune it to taste.** Custom `gpu-screen-recorder` flags and arguments, 8-bit or 10-bit colour, color themes, and fully rebindable hotkeys, all from Settings.

## Using Vice

| Key / Action | What happens |
|---|---|
| **F9** | Save the last 20 s |
| **Extra clip keys** | Save their own duration, e.g. F6 for 60 s (Settings → Hotkeys) |
| **Key combos** | Rebind to a modifier combo like **Alt + F9** (Settings → Hotkeys) |
| **F9 · F9** (double-tap) | Start / stop a session recording |
| **F9** during a session | Drop a highlight at this moment |
| **Click a thumbnail** | Open viewer · ← → next/prev · **H** new highlight · **Esc** close |
| **Share** | Copy the public URL (pastes into Discord as a playable embed) |
| **Trim** | Drag handles to crop a clip in place |

Clips live in `~/Videos/Vice/`. Closing the browser tab keeps the daemon recording; reopen with `flatpak run io.github.eklonofficial.Vice` any time.

## Compatibility

This fork has been built and tested specifically for **Bazzite on KDE Plasma
(Wayland)**. A couple of upstream compatibility notes don't apply here:

| Compositor | Status on this fork |
|---|---|
| KDE Plasma (Wayland) | ✅ tested — `backend = "auto"` picks `gpu-screen-recorder` |
| Other Wayland compositors (Hyprland, sway, GNOME) | should work, but `wf-recorder` needs `wlr-screencopy` support, which KDE lacks |
| X11 | untested on this fork |

`gpu-screen-recorder` is the only backend actually exercised on Bazzite/KDE;
`wf-recorder` and `ffmpeg x11grab` remain available as opt-ins via
`recording.backend` but aren't guaranteed to work on every desktop.

## CLI

```
flatpak run io.github.eklonofficial.Vice start          Start the recording daemon
flatpak run io.github.eklonofficial.Vice start --no-open-ui
                                                          Start the daemon without opening the browser UI
flatpak run io.github.eklonofficial.Vice stop            Stop the daemon
flatpak run io.github.eklonofficial.Vice clip            Save a clip right now
flatpak run io.github.eklonofficial.Vice status          Show daemon status, backend, and share URL
flatpak run io.github.eklonofficial.Vice clips           List saved clips
flatpak run io.github.eklonofficial.Vice config          Print current config
flatpak run io.github.eklonofficial.Vice list-keys       Show valid hotkey names (KEY_F9, KEY_INSERT, …)
flatpak run io.github.eklonofficial.Vice doctor          Run startup diagnostics
```

## Configuration

Vice writes `~/.config/vice/config.toml` on first run. Everything below is also editable live from the GUI.

```toml
[recording]
buffer_duration = 120     # seconds kept in the rolling buffer
clip_duration   = 20      # seconds saved per clip
fps             = 60
display         = "DP-1"  # optional; omit to use the backend default display
follow_mouse_display = false # record whichever monitor the pointer is on, ignoring `display`
encoder         = "auto"  # auto | h264_nvenc | hevc_nvenc | h264_vaapi | hevc_vaapi | libx264 | libx265
color_depth     = "8"     # 8 | 10 (10-bit needs an HEVC or AV1 encoder)
backend         = "auto"  # auto | gsr | wf-recorder | ffmpeg  -- leave as "auto" on Bazzite/KDE
container       = "mp4"   # mp4 | mkv (mkv is crash-safe; Discord embeds need mp4)
capture_audio   = true
capture_microphone = false
microphone_source = "default_input"
gsr_audio_source = "default_output"
audio_tracks    = []
audio_tracks_mix_first = false
gsr_args        = ""

[hotkeys]
clip = "KEY_F9"
disable_while_focused = []

[[hotkeys.clip_presets]]
key = "KEY_F6"
duration = 60

[output]
directory = "~/Videos/Vice"
tag_clips_with_game   = true
auto_playlist_by_game = true
clip_name_template    = ""

[sharing]
enabled           = true
port              = 8765
public_port       = 8766
cloudflare_tunnel = true
base_url          = ""

[discord]
enabled            = true
client_id_override = ""

[updates]
check_on_start = true

[notifications]
sound_volume = 1.0
```

## Troubleshooting

**Where do I even start?** `flatpak run io.github.eklonofficial.Vice doctor` — it checks the config, the recorder backend, dependency wrappers, and the daemon's HTTP/IPC status in one shot.

**F9 doesn't do anything.** Check hotkey access:
```bash
flatpak run --command=sh io.github.eklonofficial.Vice -c "ls -la /dev/input/"
```
If that errors out, re-run `./install-bazzite.sh` to reinstall the udev rule, then log out and back in.

**Clip button times out / "Timed out waiting for GSR to write clip".** Almost
always one of two things on this fork:
- `~/Videos` isn't the same folder as your (possibly localized) `$XDG_VIDEOS_DIR`.
  Check with `flatpak run --command=sh io.github.eklonofficial.Vice -c "ls ~/Videos/Vice/"` —
  if that errors, run `flatpak override --user --filesystem=~/Videos:create io.github.eklonofficial.Vice`.
- Stale `gpu-screen-recorder` processes from a previous crashed session are still
  running and catching the save signal. `pkill -9 -f "^gpu-screen-recorder"` and
  relaunch Vice.

**Clips are unreadable / thumbnail shows "unreadable" in the UI.** This is a
missing H.264 decoder on the host, not a Vice bug — Fedora ships `ffmpeg-free`
by default, which has the H.264 decoder removed for patent reasons. Check:
```bash
ffmpeg -decoders 2>/dev/null | grep -i h264
```
If you only see `libopenh264` (no plain `h264` line), swap to full ffmpeg:
```bash
rpm-ostree override remove ffmpeg-free --install ffmpeg
systemctl reboot
```
`./install-bazzite.sh` does this automatically on a fresh install, but a system
update can occasionally reintroduce `ffmpeg-free` — re-run the installer if clips
stop reading after an update.

**Address already in use on port 8765.** A previous daemon didn't shut down cleanly:
```bash
flatpak run io.github.eklonofficial.Vice stop
pkill -9 -f "vice start"
rm -f /tmp/vice/vice.pid /tmp/vice/vice.sock
flatpak run io.github.eklonofficial.Vice
```

**No native window, always opens in the browser.** Intentional on this fork —
the Flatpak build skips `pywebview`/WebKit entirely to avoid bundling a whole
browser engine in the sandbox. `vice-app` already falls back to your system
browser when pywebview isn't installed, so that's what you get. Functionally
identical, just a browser tab instead of an app window.

**Share link only works on my local network.** Enable the tunnel in Settings → Sharing. `cloudflared` runs on the host via the same Flatpak bridge as the other tools — if the tunnel fails to start, check that `cloudflared` is reachable with `flatpak run --command=sh io.github.eklonofficial.Vice -c "cloudflared --version"`.

**Clip won't embed on Discord.** Discord only inlines videos up to about 50 MB; trim the clip or lower CRF/resolution. Links also stop working when the Vice daemon restarts, since a fresh tunnel URL is generated each run; repost the link after a restart. MKV clips don't embed; use the default MP4 container for sharing.

**Anything else.** Run `flatpak run io.github.eklonofficial.Vice doctor` for full diagnostics, check `~/.local/share/vice/vice.log`, or see the long-form notes in [`flatpak/BAZZITE.md`](flatpak/BAZZITE.md).

---

## Credits

Based on **Vice** by **Andrew Marin** ([github.com/eklonofficial](https://github.com/eklonofficial)) — [upstream repo](https://github.com/eklonofficial/Vice). This fork only adds Bazzite/Flatpak packaging on top; all the actual clipping, editing, and sharing functionality is upstream's work.

## License

[GPL-3.0](LICENSE)