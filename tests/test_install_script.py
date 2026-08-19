import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "install.sh"


class InstallScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = INSTALL_SH.read_text()

    def test_gsr_source_build_uses_pinned_refs_and_override(self) -> None:
        script = self.script

        self.assertIn('GSR_DEFAULT_REF="5.13.3"', script)
        self.assertIn('GSR_FFMPEG6_REF="5.12.5"', script)
        self.assertIn('VICE_GSR_REF:-', script)
        self.assertIn("major < 59", script)
        self.assertIn('git clone --depth 1 --branch "$gsr_ref" "$GSR_REPO_URL" "$tmpdir"', script)

    def test_rpm_ostree_guard_runs_before_package_manager_detection(self) -> None:
        script = self.script

        self.assertIn("/run/ostree-booted", script)
        self.assertIn("rpm-ostree", script)
        self.assertIn("Bazzite / Fedora Atomic", script)
        self.assertIn("Silverblue", script)
        self.assertIn("dnf is not the right install path", script)
        self.assertLess(script.index("if is_rpm_ostree_system; then"), script.index("detect_package_manager()"))

    def test_gsr_build_runs_as_user_with_sudo_only_for_install(self) -> None:
        """Regression test for #84: building under sudo left a root-owned
        tree in /tmp that cleanup could not delete."""
        script = self.script

        # The upstream installer (which runs everything as root) is gone.
        self.assertNotIn("sudo ./install.sh", script)
        # Build steps run unprivileged; only meson install is elevated.
        self.assertIn("meson setup build", script)
        self.assertNotIn("sudo meson setup", script)
        self.assertNotIn("sudo ninja", script)
        self.assertIn("sudo meson install -C build", script)
        # Cleanup has a sudo fallback for any root-owned leftovers.
        self.assertIn('rm -rf "$tmpdir" 2>/dev/null || sudo rm -rf "$tmpdir"', script)

    def test_fedora_ffmpeg_devel_matches_installed_ffmpeg(self) -> None:
        """Regression test for #115: RPM Fusion systems have ffmpeg, not
        ffmpeg-free, so the -devel package must match."""
        script = self.script

        self.assertIn("_fedora_ffmpeg_devel()", script)
        self.assertIn("rpm -q ffmpeg &>/dev/null", script)
        self.assertIn("printf 'ffmpeg-devel\\n'", script)
        self.assertIn("printf 'ffmpeg-free-devel\\n'", script)

        match = re.search(
            r"dnf\)\s+local ffmpeg_devel.*?_dnf_install_best_effort (?P<packages>.*?)\n\s+;;",
            script,
            flags=re.S,
        )
        self.assertIsNotNone(match)
        self.assertIn('"$ffmpeg_devel"', match.group("packages"))
        self.assertNotIn("ffmpeg-free-devel", match.group("packages"))

    def test_clipboard_tools_installed_per_session_type(self) -> None:
        script = self.script

        self.assertIn("wl-clipboard", script)
        self.assertIn("xclip", script)
        # Present in every package-manager branch.
        for mgr in ("apt-get install -y wl-clipboard",
                    "dnf install -y wl-clipboard",
                    "zypper install -y wl-clipboard"):
            self.assertIn(mgr, script)

    def test_apt_gsr_build_deps_include_upstream_required_headers(self) -> None:
        match = re.search(
            r"apt\)\s+sudo apt-get install -y (?P<packages>.*?) \|\| return 1",
            self.script,
            flags=re.S,
        )
        self.assertIsNotNone(match)
        packages = set(re.findall(r"[A-Za-z0-9_.+-]+", match.group("packages")))

        required = {
            "build-essential",
            "linux-libc-dev",
            "libx11-dev",
            "libavfilter-dev",
            "libva-dev",
            "libcap-dev",
            "libdbus-1-dev",
            "libvulkan-dev",
            "libspa-0.2-dev",
            "libpipewire-0.3-dev",
            "libavcodec-dev",
            "libavformat-dev",
            "libavutil-dev",
            "libswresample-dev",
        }
        self.assertTrue(required.issubset(packages), required - packages)

    def test_dnf_gsr_build_deps_match_the_apt_branch(self) -> None:
        """Fedora was missing a C++ compiler, libva, vulkan and libcap, so the
        source build failed one meson check at a time."""
        match = re.search(
            r"dnf\)\s+local ffmpeg_devel.*?_dnf_install_best_effort (?P<packages>.*?)\n\s+;;",
            self.script,
            flags=re.S,
        )
        self.assertIsNotNone(match)
        packages = set(re.findall(r"[A-Za-z0-9_.+-]+", match.group("packages")))

        required = {"gcc-c++", "libva-devel", "vulkan-loader-devel", "libcap-devel"}
        self.assertTrue(required.issubset(packages), required - packages)

    def test_dnf_build_deps_retry_individually(self) -> None:
        # One unavailable name on an unusual arch used to abort the whole
        # transaction before meson could report the real missing dependency.
        self.assertIn("_dnf_install_best_effort()", self.script)
        self.assertIn('sudo dnf install -y "$pkg"', self.script)

    def test_fedora_qtpy_package_uses_capitalised_name(self) -> None:
        # Fedora ships python3-QtPy and dnf5 matches case-sensitively.
        self.assertIn("sudo dnf install -y python3-QtPy", self.script)
        # Installing the Qt stack as one command meant the case mismatch also
        # dropped PyQt6 and QtWebEngine to PyPI wheels.
        self.assertNotIn(
            "python3-pyqt6 python3-pyqt6-webengine python3-qtpy >/dev/null", self.script
        )

    def test_cloudflared_rpm_matches_machine_architecture(self) -> None:
        self.assertIn('cloudflared-linux-${_cf_arch}.rpm', self.script)
        self.assertIn("aarch64|arm64) _cf_arch=arm64", self.script)

    def test_no_stale_serveo_references(self) -> None:
        # serveo was removed as a tunnel in v1.3.3, but the installer still
        # promised it as a fallback, which confused the reporter of #105.
        self.assertNotIn("serveo", self.script.lower())


class PackagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = INSTALL_SH.read_text()

    def test_aur_package_ships_user_service(self) -> None:
        """Regression test for #116: the AUR package installed no systemd
        unit, so the daemon never started at login."""
        service = (REPO_ROOT / "packaging" / "vice.service").read_text()
        self.assertIn("ExecStart=/usr/bin/vice start --no-open-ui", service)
        self.assertIn("WantedBy=graphical-session.target", service)
        self.assertIn("PassEnvironment=WAYLAND_DISPLAY DISPLAY", service)

        pkgbuild = (REPO_ROOT / "PKGBUILD").read_text()
        self.assertIn("packaging/vice.service", pkgbuild)
        self.assertIn("/usr/lib/systemd/user/vice.service", pkgbuild)
        self.assertIn("install=vice-clipper.install", pkgbuild)

    def test_clipboard_and_tunnel_tools_are_hard_dependencies(self) -> None:
        """Copy-to-clipboard and public links silently failed on the AUR
        package because these were only optdepends."""
        import re
        pkgbuild = (REPO_ROOT / "PKGBUILD").read_text()
        depends = re.search(r"depends=\((.*?)\)", pkgbuild, flags=re.S).group(1)
        optdepends = re.search(r"optdepends=\((.*?)\)", pkgbuild, flags=re.S).group(1)
        for pkg in ("wl-clipboard", "xclip", "cloudflared"):
            self.assertIn(f"'{pkg}'", depends)
            self.assertNotIn(f"'{pkg}:", optdepends)
        self.assertIn(
            "systemctl --user enable --now vice.service",
            (REPO_ROOT / "vice-clipper.install").read_text(),
        )

    def test_window_detection_tools_are_hard_dependencies(self) -> None:
        """Game tagging, auto playlists and Discord presence all read the
        focused window through these, and neither install path shipped them,
        so detection silently found nothing (#152)."""
        pkgbuild = (REPO_ROOT / "PKGBUILD").read_text()
        depends = re.search(r"depends=\((.*?)\)", pkgbuild, flags=re.S).group(1)
        for pkg in ("xdotool", "xorg-xprop", "wmctrl"):
            self.assertIn(f"'{pkg}'", depends)
        self.assertIn("xdotool xorg-xprop wmctrl", self.script)
        # The other package managers spell xprop differently.
        for branch in ("xdotool x11-utils wmctrl", "xdotool xprop wmctrl"):
            self.assertIn(branch, self.script)

    def test_nvidia_utils_is_not_forced_over_a_legacy_branch(self) -> None:
        """nvidia-smi answering already proves a driver userspace is there.
        Asking for nvidia-utils by name collided with nvidia-580xx-utils and
        aborted the whole install (#147)."""
        self.assertIn("^nvidia(-[0-9]+xx)?-utils$", self.script)
        # The add still exists, but only behind the already-installed check.
        guard = self.script.index("^nvidia(-[0-9]+xx)?-utils$")
        add = self.script.index("pkgs+=(nvidia-utils)")
        self.assertLess(guard, add)

    def test_service_is_reenabled_so_wantedby_changes_take_effect(self) -> None:
        """enable leaves an existing unit's old symlinks in place, so the new
        default.target want never appeared on upgrades (#139)."""
        self.assertIn("systemctl --user reenable vice.service", self.script)
        self.assertIn(
            "systemctl --user reenable vice.service",
            (REPO_ROOT / "vice-clipper.install").read_text(),
        )


if __name__ == "__main__":
    unittest.main()
