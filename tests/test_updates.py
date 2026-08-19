import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vice import updates

# The real body of the v2.3.0 release, which is the format the summariser
# has to cope with.
REAL_BODY = """## Features

- **Auto playlists are yours to manage now.** The per-game playlists Vice makes
  for you can be renamed, recoloured, given an emoji, and deleted.
- **Clips can be filed by hand.** Right-click a clip or drag it onto a playlist.

## Fixes

- **Filename tagging kept working after a settings change (#121).** Changing a
  recording setting used to silently stop game tagging.
"""


class VersionTests(unittest.TestCase):
    def test_parse_version(self) -> None:
        self.assertEqual(updates.parse_version("v2.4.0"), (2, 4, 0))
        self.assertEqual(updates.parse_version("2.4"), (2, 4))
        self.assertEqual(updates.parse_version(" 2.4.0 "), (2, 4, 0))
        self.assertIsNone(updates.parse_version("nightly"))
        self.assertIsNone(updates.parse_version(""))
        self.assertIsNone(updates.parse_version(None))

    def test_is_newer(self) -> None:
        self.assertTrue(updates.is_newer("2.5.0", "2.4.0"))
        self.assertTrue(updates.is_newer("v2.4.1", "2.4.0"))
        self.assertTrue(updates.is_newer("3.0", "2.9.9"))
        self.assertFalse(updates.is_newer("2.4.0", "2.4.0"))
        self.assertFalse(updates.is_newer("2.3.0", "2.4.0"))
        self.assertFalse(updates.is_newer("junk", "2.4.0"))
        self.assertFalse(updates.is_newer("2.5.0", "junk"))

    def test_a_dev_build_ahead_of_the_latest_tag_stays_quiet(self) -> None:
        # Andrew runs unreleased builds; being ahead must never prompt.
        self.assertFalse(updates.is_newer("2.3.0", "2.4.0"))
        self.assertIsNone(updates.available({"version": "2.3.0"}, installed="2.4.0"))


class SummariseTests(unittest.TestCase):
    def test_pulls_the_bold_lead_ins(self) -> None:
        notes = updates.summarize_notes(REAL_BODY)
        self.assertEqual(len(notes), 3)
        self.assertEqual(notes[0], "Auto playlists are yours to manage now")
        self.assertNotIn("**", " ".join(notes))
        self.assertNotIn("#", " ".join(notes))

    def test_limit_and_truncation(self) -> None:
        self.assertEqual(len(updates.summarize_notes(REAL_BODY, limit=2)), 2)
        long_body = "- **" + ("word " * 60).strip() + ".** tail"
        line = updates.summarize_notes(long_body)[0]
        self.assertLessEqual(len(line), updates.NOTE_CHARS + 1)
        self.assertTrue(line.endswith("…"))

    def test_falls_back_to_plain_bullets(self) -> None:
        notes = updates.summarize_notes("## Fixes\n\n- Fixed the thing\n- And another\n")
        self.assertEqual(notes, ["Fixed the thing", "And another"])

    def test_empty_body(self) -> None:
        self.assertEqual(updates.summarize_notes(""), [])
        self.assertEqual(updates.summarize_notes("Just prose, no bullets."), [])


class FetchTests(unittest.TestCase):
    def _resp(self, payload, status=200, etag='W/"abc"'):
        resp = mock.MagicMock()
        resp.status = status
        resp.read.return_value = json.dumps(payload).encode()
        resp.headers = {"ETag": etag}
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda *a: False
        return resp

    def test_parses_a_release(self) -> None:
        payload = {"tag_name": "v2.5.0", "html_url": "https://example/rel",
                   "body": REAL_BODY, "draft": False, "prerelease": False}
        with mock.patch.object(updates, "urlopen", return_value=self._resp(payload)):
            got = updates.fetch_latest()
        self.assertEqual(got["version"], "2.5.0")
        self.assertEqual(got["url"], "https://example/rel")
        self.assertEqual(got["etag"], 'W/"abc"')
        self.assertTrue(got["notes"])

    def test_sends_the_cached_etag(self) -> None:
        payload = {"tag_name": "v2.5.0", "draft": False, "prerelease": False}
        with mock.patch.object(updates, "urlopen", return_value=self._resp(payload)) as op:
            updates.fetch_latest(etag='W/"prev"')
        request = op.call_args[0][0]
        self.assertEqual(request.headers.get("If-none-match"), 'W/"prev"')

    def test_uninteresting_replies_are_none(self) -> None:
        cases = [
            ({"tag_name": "v2.5.0"}, 304),
            ({"tag_name": "v2.5.0", "draft": True}, 200),
            ({"tag_name": "v2.5.0", "prerelease": True}, 200),
            ({"tag_name": "nightly"}, 200),
            ({"message": "rate limit exceeded"}, 200),
        ]
        for payload, status in cases:
            with mock.patch.object(updates, "urlopen",
                                   return_value=self._resp(payload, status=status)):
                self.assertIsNone(updates.fetch_latest(), payload)

    def test_network_failure_never_propagates(self) -> None:
        with mock.patch.object(updates, "urlopen", side_effect=OSError("no route")):
            self.assertIsNone(updates.fetch_latest())
        with mock.patch.object(updates, "urlopen", side_effect=ValueError("nonsense")):
            self.assertIsNone(updates.fetch_latest())


class CacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = updates.UpdateCache(Path(self.tmp.name) / "update.json")

    def test_round_trip_and_unreadable_file(self) -> None:
        self.assertEqual(self.cache.load(), {})
        self.cache.save({"version": "2.5.0", "checked_at": 100})
        self.assertEqual(self.cache.load()["version"], "2.5.0")
        self.cache.path.write_text("{not json")
        self.assertEqual(self.cache.load(), {})

    def test_staleness(self) -> None:
        self.assertTrue(self.cache.stale())
        self.cache.save({"checked_at": 1000})
        self.assertFalse(self.cache.stale(now=1000 + updates.CHECK_INTERVAL - 1))
        self.assertTrue(self.cache.stale(now=1000 + updates.CHECK_INTERVAL))
        self.cache.save({"checked_at": "soon"})
        self.assertTrue(self.cache.stale())


class AvailableTests(unittest.TestCase):
    def test_shape(self) -> None:
        got = updates.available(
            {"version": "2.5.0", "url": "https://example/rel", "notes": ["a"]},
            installed="2.4.0")
        self.assertEqual(got, {"version": "2.5.0", "url": "https://example/rel",
                               "notes": ["a"]})

    def test_nothing_to_report(self) -> None:
        self.assertIsNone(updates.available({}, installed="2.4.0"))
        self.assertIsNone(updates.available({"version": "2.4.0"}, installed="2.4.0"))


if __name__ == "__main__":
    unittest.main()
