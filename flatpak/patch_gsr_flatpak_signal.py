#!/usr/bin/env python3
"""
One-shot patch: makes Vice's GSR "save replay" signal work when
gpu-screen-recorder is itself installed as a nested Flatpak
(com.dec05eba.gpu_screen_recorder) inside Vice's own Flatpak sandbox.

Run once from the Vice project root:
    python3 patch_gsr_flatpak_signal.py
"""
import pathlib
import sys

TARGET = pathlib.Path("vice/recorder.py")

HELPER_MARKER = "def _flatpak_signal_gsr("
HELPER_CODE = '''
def _flatpak_signal_gsr(sig_name: str) -> bool:
    """Send *sig_name* (e.g. 'SIGUSR1') to the real gpu-screen-recorder
    process on the host, bypassing the local (sandboxed) PID entirely.

    Only used when running inside Flatpak (VICE_RUNNING_IN_FLATPAK=1).
    GSR may itself be installed as a separate, nested Flatpak
    (com.dec05eba.gpu_screen_recorder), in which case a signal sent to
    the local subprocess handle only reaches flatpak-spawn's own client
    stub -- not the real gpu-screen-recorder process, which lives
    several sandbox layers away on the host. Routing through a host-side
    `pkill` (matched via `^gpu-screen-recorder` so it can never match the
    outer `flatpak run --command=...` launcher) reaches it directly,
    the same way upstream's own docs recommend controlling GSR.
    """
    try:
        result = subprocess.run(
            ["flatpak-spawn", "--host", "pkill", f"-{sig_name}", "-f", "^gpu-screen-recorder"],
            capture_output=True,
            timeout=5,
        )
        # pkill exits 1 when nothing matched -- still means the relay itself worked.
        return result.returncode in (0, 1)
    except Exception:
        log.exception("flatpak-spawn pkill relay for %s failed", sig_name)
        return False


'''

OLD_SNIPPET = '''        log.info("Sending SIGUSR1 to GSR (pid=%d) to save replay", self._proc.pid)
        try:
            os.kill(self._proc.pid, signal.SIGUSR1)
        except ProcessLookupError:
            self.last_clip_error = "The recorder stopped before the clip could be saved."
            log.error("GSR process not found")
            return None
'''

NEW_SNIPPET = '''        log.info("Sending SIGUSR1 to GSR (pid=%d) to save replay", self._proc.pid)
        if os.environ.get("VICE_RUNNING_IN_FLATPAK"):
            if not _flatpak_signal_gsr("SIGUSR1"):
                self.last_clip_error = "Could not signal gpu-screen-recorder on the host."
                log.error("flatpak host pkill SIGUSR1 relay failed")
                return None
        else:
            try:
                os.kill(self._proc.pid, signal.SIGUSR1)
            except ProcessLookupError:
                self.last_clip_error = "The recorder stopped before the clip could be saved."
                log.error("GSR process not found")
                return None
'''

CLASS_MARKER = "class GSRRecorder(Recorder):"


def main() -> int:
    if not TARGET.exists():
        print(f"error: {TARGET} not found -- run this from the Vice project root "
              f"(the folder that contains pyproject.toml and vice/).", file=sys.stderr)
        return 1

    text = TARGET.read_text()

    if HELPER_MARKER in text:
        print("Helper function already present -- skipping that part.")
    else:
        idx = text.find(CLASS_MARKER)
        if idx == -1:
            print(f"error: could not find '{CLASS_MARKER}' in {TARGET}", file=sys.stderr)
            return 1
        text = text[:idx] + HELPER_CODE + text[idx:]
        print("Inserted _flatpak_signal_gsr() helper before GSRRecorder class.")

    if NEW_SNIPPET in text:
        print("save_clip() already patched -- skipping that part.")
    elif OLD_SNIPPET in text:
        text = text.replace(OLD_SNIPPET, NEW_SNIPPET, 1)
        print("Patched GSRRecorder.save_clip() to use the flatpak relay.")
    else:
        print("error: could not find the expected SIGUSR1 snippet to replace -- "
              "the file may differ from what this patch expects. No changes made "
              "beyond the helper (if it was inserted above).", file=sys.stderr)
        TARGET.write_text(text)  # still save the helper insertion if any
        return 1

    TARGET.write_text(text)
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
