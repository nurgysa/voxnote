# tests/test_processing_sources.py
import os

from processing import sources


def test_archive_copy_leaves_original(tmp_path):
    src = tmp_path / "rec.m4a"
    src.write_bytes(b"abc")
    dest = tmp_path / "sources"
    out = sources.archive_audio(str(src), str(dest), "2026-06-14_1000_call", move=False)
    assert out == os.path.join(
        str(dest),
        "Audio",
        "VoxNote",
        "Meetings",
        "2026-06-14",
        "2026-06-14_1000_call.m4a",
    )
    assert os.path.isfile(out)
    assert src.exists()  # copy leaves external picked originals in place
    assert (
        dest / "Audio" / "VoxNote" / "Meetings" / "2026-06-14" / "2026-06-14_1000_call.m4a"
    ).read_bytes() == b"abc"


def test_archive_move_removes_original(tmp_path):
    src = tmp_path / "rec.mp3"
    src.write_bytes(b"x")
    dest = tmp_path / "sources"
    out = sources.archive_audio(str(src), str(dest), "m", move=True)
    assert out.endswith(os.path.join("Audio", "VoxNote", "Meetings", "undated", "m.mp3"))
    assert os.path.isfile(out)
    assert not src.exists()  # moved


def test_archive_collision_safe(tmp_path):
    dest = tmp_path / "sources"
    organized = dest / "Audio" / "VoxNote" / "Meetings" / "undated"
    organized.mkdir(parents=True)
    (organized / "m.m4a").write_bytes(b"old")
    src = tmp_path / "rec.m4a"
    src.write_bytes(b"new")
    out = sources.archive_audio(str(src), str(dest), "m", move=False)
    assert out.endswith("m-2.m4a")
    assert (organized / "m.m4a").read_bytes() == b"old"  # never overwritten


def test_archive_never_writes_audio_to_sources_root(tmp_path):
    src = tmp_path / "meeting.wav"
    src.write_bytes(b"audio")
    dest = tmp_path / "sources"

    out = sources.archive_audio(str(src), str(dest), "2026-07-04_1429_meeting", move=False)

    assert os.path.dirname(out) == os.path.join(
        str(dest), "Audio", "VoxNote", "Meetings", "2026-07-04"
    )
    assert not (dest / "2026-07-04_1429_meeting.wav").exists()


def test_archive_rehomes_file_already_in_sources_root(tmp_path):
    dest = tmp_path / "sources"
    dest.mkdir()
    src = dest / "loose-root-audio.m4a"
    src.write_bytes(b"audio")

    out = sources.archive_audio(str(src), str(dest), "2026-07-04_1500_loose-root-audio", move=False)

    assert out.endswith(
        os.path.join(
            "Audio",
            "VoxNote",
            "Meetings",
            "2026-07-04",
            "2026-07-04_1500_loose-root-audio.m4a",
        )
    )
    assert os.path.isfile(out)
    assert not src.exists()


def test_archive_reuses_audio_already_in_organized_archive(tmp_path):
    dest = tmp_path / "sources"
    organized = dest / "Audio" / "VoxNote" / "Meetings" / "2026-07-04"
    organized.mkdir(parents=True)
    src = organized / "2026-07-04_1009_запись-автосохранение.m4a"
    src.write_bytes(b"audio")

    out = sources.archive_audio(
        str(src),
        str(dest),
        "2026-07-04_1429_2026-07-04_1009_запись-автосохранение",
        move=False,
    )

    assert out == str(src)
    assert src.read_bytes() == b"audio"
    assert sorted(p.name for p in organized.iterdir()) == [src.name]
