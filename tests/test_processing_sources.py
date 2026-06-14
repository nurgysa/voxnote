# tests/test_processing_sources.py
import os

from processing import sources


def test_archive_copy_leaves_original(tmp_path):
    src = tmp_path / "rec.m4a"
    src.write_bytes(b"abc")
    dest = tmp_path / "sources"
    out = sources.archive_audio(str(src), str(dest), "2026-06-14_1000_call", move=False)
    assert out == os.path.join(str(dest), "2026-06-14_1000_call.m4a")
    assert os.path.isfile(out)
    assert src.exists()  # copy leaves the original
    assert (dest / "2026-06-14_1000_call.m4a").read_bytes() == b"abc"


def test_archive_move_removes_original(tmp_path):
    src = tmp_path / "rec.mp3"
    src.write_bytes(b"x")
    dest = tmp_path / "sources"
    out = sources.archive_audio(str(src), str(dest), "m", move=True)
    assert out.endswith("m.mp3")
    assert os.path.isfile(out)
    assert not src.exists()  # moved


def test_archive_collision_safe(tmp_path):
    dest = tmp_path / "sources"
    dest.mkdir()
    (dest / "m.m4a").write_bytes(b"old")
    src = tmp_path / "rec.m4a"
    src.write_bytes(b"new")
    out = sources.archive_audio(str(src), str(dest), "m", move=False)
    assert out.endswith("m-2.m4a")
    assert (dest / "m.m4a").read_bytes() == b"old"  # never overwritten
