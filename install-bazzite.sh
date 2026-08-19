#!/usr/bin/env bash
#
# install-bazzite.sh -- one-shot, idempotent installer for running Vice
# as a Flatpak on Bazzite (or any rpm-ostree / KDE Plasma Wayland setup).
#
# Safe to re-run: every step checks whether it's already done before
# doing anything. Run it again after a reboot and it'll pick up where
# it left off.
#
# Usage:
#   cd Vice/            (the folder with pyproject.toml)
#   ./install-bazzite.sh
#
set -uo pipefail

# ── pretty output ──────────────────────────────────────────────────────
c_reset=$'\033[0m'; c_bold=$'\033[1m'
c_green=$'\033[32m'; c_yellow=$'\033[33m'; c_red=$'\033[31m'; c_blue=$'\033[34m'
ok()    { printf '%s[OK]%s   %s\n'   "$c_green"  "$c_reset" "$*"; }
info()  { printf '%s[..]%s   %s\n'   "$c_blue"   "$c_reset" "$*"; }
warn()  { printf '%s[!!]%s   %s\n'   "$c_yellow" "$c_reset" "$*"; }
err()   { printf '%s[ERR]%s  %s\n'   "$c_red"    "$c_reset" "$*" >&2; }
step()  { printf '\n%s%s%s\n' "$c_bold" "== $* ==" "$c_reset"; }

REBOOT_NEEDED=0
GSR_APP_ID="com.dec05eba.gpu_screen_recorder"

# ── sanity: run from the project root ──────────────────────────────────
if [[ ! -f pyproject.toml ]] || [[ ! -d flatpak ]]; then
    err "Run this from the Vice project root (the folder with pyproject.toml and flatpak/)."
    exit 1
fi
PROJECT_ROOT="$(pwd)"

# ── warn if a reboot is already pending from something else ───────────
if command -v rpm-ostree >/dev/null 2>&1; then
    if rpm-ostree status --json 2>/dev/null | grep -q '"staged": true'; then
        warn "rpm-ostree already has a staged deployment pending from something else."
        warn "Reboot first (systemctl reboot), then re-run this script."
        exit 0
    fi
fi

# ═════════════════════════════════════════════════════════════════════
step "1/7 -- Legacy (non-Flatpak) install cleanup"
# ═════════════════════════════════════════════════════════════════════
LEGACY_FOUND=0
if systemctl --user is-enabled vice.service >/dev/null 2>&1; then
    LEGACY_FOUND=1
fi
if [[ -d "$HOME/.local/share/vice/venv" ]]; then
    LEGACY_FOUND=1
fi

if [[ "$LEGACY_FOUND" -eq 1 ]]; then
    warn "Found a previous non-Flatpak Vice install (install.sh)."
    read -r -p "    Disable and remove it now? [Y/n] " reply
    reply=${reply:-Y}
    if [[ "$reply" =~ ^[Yy] ]]; then
        systemctl --user disable --now vice.service >/dev/null 2>&1 || true
        pkill -f "\.local/share/vice/venv" 2>/dev/null || true
        if [[ -x "$HOME/.local/share/vice/venv/bin/vice" ]]; then
            "$HOME/.local/share/vice/venv/bin/vice" uninstall >/dev/null 2>&1 || true
        fi
        rm -rf "$HOME/.local/share/vice/venv"
        rm -f "$HOME/.local/share/applications/vice.desktop"
        rm -f "$HOME/.config/systemd/user/vice.service"
        systemctl --user daemon-reload >/dev/null 2>&1 || true
        ok "Legacy install removed."
    else
        warn "Leaving the legacy install in place -- it may conflict (same log file, same port)."
    fi
else
    ok "No legacy (non-Flatpak) install found."
fi

# ═════════════════════════════════════════════════════════════════════
step "2/7 -- Host system packages (rpm-ostree)"
# ═════════════════════════════════════════════════════════════════════
NEEDED_PKGS=()
for pkg_bin in "flatpak-builder:flatpak-builder" "xdotool:xdotool" "xprop:xorg-x11-utils" "wmctrl:wmctrl" "wf-recorder:wf-recorder"; do
    bin="${pkg_bin%%:*}"; pkg="${pkg_bin##*:}"
    if command -v "$bin" >/dev/null 2>&1; then
        ok "$bin already available."
    else
        info "$bin missing -- will install '$pkg'."
        NEEDED_PKGS+=("$pkg")
    fi
done

FFMPEG_SWAP_NEEDED=0
if rpm -q ffmpeg >/dev/null 2>&1 && ! rpm -q ffmpeg-free >/dev/null 2>&1; then
    ok "Full ffmpeg (with h264 decode) already installed."
elif ffmpeg -decoders 2>/dev/null | grep -qE '^\s*V\S*D\s+h264\s'; then
    ok "A working native h264 decoder is already available."
else
    info "No working h264 decoder found -- will swap ffmpeg-free for full ffmpeg."
    FFMPEG_SWAP_NEEDED=1
fi

if [[ ${#NEEDED_PKGS[@]} -gt 0 ]]; then
    info "Layering: ${NEEDED_PKGS[*]}"
    if ! rpm-ostree install "${NEEDED_PKGS[@]}"; then
        err "rpm-ostree install failed -- see the error above."
        exit 1
    fi
    REBOOT_NEEDED=1
fi

if [[ "$FFMPEG_SWAP_NEEDED" -eq 1 ]]; then
    info "Swapping ffmpeg-free -> ffmpeg (needed for clip validation/thumbnails)."
    if ! rpm-ostree override remove ffmpeg-free --install ffmpeg; then
        err "ffmpeg swap failed -- see the error above."
        exit 1
    fi
    REBOOT_NEEDED=1
fi

if [[ "$REBOOT_NEEDED" -eq 1 ]]; then
    warn "System packages were staged and need a reboot to become active."
    warn "Run:  systemctl reboot"
    warn "...then re-run this script (./install-bazzite.sh) to continue."
    exit 0
fi

# ═════════════════════════════════════════════════════════════════════
step "3/7 -- udev rule for hotkeys (/dev/input access)"
# ═════════════════════════════════════════════════════════════════════
UDEV_RULE=/etc/udev/rules.d/70-vice-input.rules
if [[ -f "$UDEV_RULE" ]] && diff -q "$PROJECT_ROOT/packaging/vice.rules" "$UDEV_RULE" >/dev/null 2>&1; then
    ok "udev rule already installed."
else
    info "Installing udev rule (needs sudo)."
    if sudo install -Dm644 "$PROJECT_ROOT/packaging/vice.rules" "$UDEV_RULE" \
        && sudo udevadm control --reload-rules \
        && sudo udevadm trigger; then
        ok "udev rule installed and reloaded."
    else
        err "Could not install the udev rule -- hotkeys may not work. Continuing anyway."
    fi
fi

# ═════════════════════════════════════════════════════════════════════
step "4/7 -- gpu-screen-recorder (Flatpak, system-wide)"
# ═════════════════════════════════════════════════════════════════════
if ! command -v flatpak >/dev/null 2>&1; then
    err "flatpak is not installed on this system. Install it first, then re-run."
    exit 1
fi

if flatpak info --system "$GSR_APP_ID" >/dev/null 2>&1; then
    ok "gpu-screen-recorder already installed system-wide."
elif flatpak info --user "$GSR_APP_ID" >/dev/null 2>&1; then
    warn "gpu-screen-recorder is installed but in --user mode."
    warn "It needs --system for the KMS capture helper's polkit rule to apply reliably."
    read -r -p "    Reinstall it as --system now? [Y/n] " reply
    reply=${reply:-Y}
    if [[ "$reply" =~ ^[Yy] ]]; then
        flatpak uninstall --user -y "$GSR_APP_ID" || true
        flatpak install --system -y flathub "$GSR_APP_ID"
    fi
else
    info "Installing gpu-screen-recorder from Flathub (--system)."
    flatpak install --system -y flathub "$GSR_APP_ID"
fi

if ! flatpak run --command=gpu-screen-recorder "$GSR_APP_ID" --version >/dev/null 2>&1; then
    warn "Could not run gpu-screen-recorder --version -- check the Flatpak install manually."
fi

# ═════════════════════════════════════════════════════════════════════
step "5/7 -- Flatpak runtimes for Vice itself"
# ═════════════════════════════════════════════════════════════════════
# org.gnome.Platform/Sdk (not bare Freedesktop): it's the runtime that
# actually ships WebKitGTK, which is what lets vice-app open a native
# window instead of falling back to the browser.
for rt in "org.gnome.Platform//46" "org.gnome.Sdk//46"; do
    if flatpak info --system "$rt" >/dev/null 2>&1 || flatpak info --user "$rt" >/dev/null 2>&1; then
        ok "$rt already installed."
    else
        info "Installing $rt."
        flatpak install --system -y flathub "$rt" || flatpak install --user -y flathub "$rt"
    fi
done

# ═════════════════════════════════════════════════════════════════════
step "6/7 -- Patch vice/recorder.py for the nested-Flatpak signal relay"
# ═════════════════════════════════════════════════════════════════════
if grep -q "_flatpak_signal_gsr" "$PROJECT_ROOT/vice/recorder.py" 2>/dev/null; then
    ok "recorder.py already patched."
else
    info "Applying patch_gsr_flatpak_signal.py."
    if python3 "$PROJECT_ROOT/flatpak/patch_gsr_flatpak_signal.py"; then
        ok "Patch applied."
    else
        err "Patch failed -- see the error above. Aborting before build."
        exit 1
    fi
fi

# ═════════════════════════════════════════════════════════════════════
step "7/7 -- Build and install the Flatpak"
# ═════════════════════════════════════════════════════════════════════
cd "$PROJECT_ROOT/flatpak"
info "Running flatpak-builder (needs network for Python deps -- can take a few minutes)."
if flatpak-builder --user --install --force-clean build-dir io.github.eklonofficial.Vice.yml; then
    ok "Flatpak built and installed."
else
    err "flatpak-builder failed -- see the error above."
    exit 1
fi

# Belt-and-suspenders: the manifest's finish-args already request this,
# but older installs that were never rebuilt won't have it until an
# override is applied explicitly.
flatpak override --user --filesystem=~/Videos:create io.github.eklonofficial.Vice >/dev/null 2>&1

# ── config: make sure we're not stuck on a backend that doesn't work ──
CONFIG_FILE="$HOME/.config/vice/config.toml"
if [[ -f "$CONFIG_FILE" ]] && grep -q '^backend = "wf-recorder"' "$CONFIG_FILE"; then
    if ! flatpak run --command=sh io.github.eklonofficial.Vice \
        -c 'true' 2>/dev/null; then
        : # can't easily test wlr-screencopy support here; just warn generically
    fi
    warn "config.toml has backend=\"wf-recorder\" pinned, which doesn't work on"
    warn "KDE Plasma Wayland (no wlr-screencopy support). Switching to \"auto\"."
    sed -i 's/^backend = "wf-recorder"/backend = "auto"/' "$CONFIG_FILE"
fi

# ── clean up any stray gpu-screen-recorder processes from past runs ───
pkill -9 -f "^gpu-screen-recorder" 2>/dev/null || true
rm -f /tmp/vice/vice.pid /tmp/vice/vice.sock 2>/dev/null || true

step "Done"
ok "Vice is installed. Launch it with:"
echo "      flatpak run io.github.eklonofficial.Vice"
echo
ok "Check everything is wired up correctly with:"
echo "      flatpak run io.github.eklonofficial.Vice doctor"
echo
info "First launch: give the replay buffer ~30-60s to fill before pressing"
info "the clip hotkey (default F9) or using the clip button in the web UI."