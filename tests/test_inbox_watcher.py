# tests/test_inbox_watcher.py
from processing.inbox_watcher import InboxWatcher, scan_inbox


def test_scan_filters_extensions_and_known(tmp_path):
    (tmp_path / "a.m4a").write_bytes(b"x")
    (tmp_path / "b.txt").write_bytes(b"x")
    (tmp_path / "c.mp3").write_bytes(b"x")
    found = scan_inbox(str(tmp_path), known={str(tmp_path / "c.mp3")})
    assert found == [str(tmp_path / "a.m4a")]


def test_poll_requires_stable_size(tmp_path):
    f = tmp_path / "rec.m4a"
    f.write_bytes(b"12345")
    w = InboxWatcher(str(tmp_path))
    assert w.poll() == []            # first sighting: record size, not ready
    assert w.poll() == [str(f)]      # size stable across two polls -> ready
    assert w.poll() == []            # already returned, not re-emitted


def test_poll_growing_file_not_ready(tmp_path):
    f = tmp_path / "rec.m4a"
    f.write_bytes(b"1")
    w = InboxWatcher(str(tmp_path))
    assert w.poll() == []            # record size 1
    f.write_bytes(b"123")            # still being written (grew)
    assert w.poll() == []            # size changed -> not ready
    assert w.poll() == [str(f)]      # now stable -> ready


def test_poll_no_dir():
    assert InboxWatcher(None).poll() == []


def test_poll_missing_dir(tmp_path):
    assert InboxWatcher(str(tmp_path / "missing")).poll() == []
