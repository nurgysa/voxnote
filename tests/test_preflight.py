from processing import preflight

# ── _parse_ffmpeg_duration (pure) ──

def test_parse_ffmpeg_duration_extracts_seconds():
    stderr = (
        "Input #0, mov,mp4, from 'x.m4a':\n"
        "  Duration: 01:02:03.50, start: 0.000000, bitrate: 128 kb/s\n"
    )
    assert preflight._parse_ffmpeg_duration(stderr) == 3723.5


def test_parse_ffmpeg_duration_none_when_absent():
    assert preflight._parse_ffmpeg_duration("no duration here") is None


# ── probe ──

def test_probe_reads_size_from_real_file(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, "_duration_via_soundfile", lambda p: None)
    monkeypatch.setattr(preflight, "_duration_via_ffmpeg", lambda p: None)
    f = tmp_path / "a.bin"
    f.write_bytes(b"0123456789")
    info = preflight.probe(str(f))
    assert info["size_bytes"] == 10
    assert info["duration_s"] is None


def test_probe_prefers_soundfile_duration(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, "_duration_via_soundfile", lambda p: 12.5)
    monkeypatch.setattr(preflight, "_duration_via_ffmpeg", lambda p: 99.0)
    f = tmp_path / "a.wav"
    f.write_bytes(b"x")
    assert preflight.probe(str(f))["duration_s"] == 12.5


def test_probe_falls_back_to_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, "_duration_via_soundfile", lambda p: None)
    monkeypatch.setattr(preflight, "_duration_via_ffmpeg", lambda p: 42.0)
    f = tmp_path / "a.m4a"
    f.write_bytes(b"x")
    assert preflight.probe(str(f))["duration_s"] == 42.0


def test_probe_missing_file_size_zero(monkeypatch):
    monkeypatch.setattr(preflight, "_duration_via_soundfile", lambda p: None)
    monkeypatch.setattr(preflight, "_duration_via_ffmpeg", lambda p: None)
    info = preflight.probe("/no/such/file.m4a")
    assert info["size_bytes"] == 0
    assert info["duration_s"] is None


# ── provider_limit_ok ──

def test_provider_limit_ok_under_cap():
    ok, reason = preflight.provider_limit_ok("AssemblyAI", 3600.0, 50 * 1024**2)
    assert ok is True
    assert reason == ""


def test_provider_limit_ok_over_cap():
    ok, reason = preflight.provider_limit_ok("Gladia", None, 5 * 1024**3)
    assert ok is False
    assert "ГБ" in reason


def test_provider_limit_ok_unknown_size_passes():
    ok, reason = preflight.provider_limit_ok("AssemblyAI", None, 0)
    assert ok is True


def test_provider_limit_ok_allows_gladia_at_135_minutes():
    ok, reason = preflight.provider_limit_ok("Gladia", 135 * 60.0, 50 * 1024**2)
    assert ok is True
    assert reason == ""


def test_provider_limit_ok_rejects_gladia_over_135_minutes_with_chunking_message():
    ok, reason = preflight.provider_limit_ok("Gladia", 136 * 60.0, 50 * 1024**2)
    assert ok is False
    assert "Gladia" in reason
    assert "135" in reason
    assert "нареж" in reason.lower() or "chunk" in reason.lower()


def test_provider_limit_ok_does_not_apply_gladia_duration_cap_to_other_providers():
    ok, reason = preflight.provider_limit_ok("AssemblyAI", 180 * 60.0, 50 * 1024**2)
    assert ok is True
    assert reason == ""


def test_provider_limit_ok_does_not_block_gladia_when_duration_unknown():
    ok, reason = preflight.provider_limit_ok("Gladia", None, 50 * 1024**2)
    assert ok is True
    assert reason == ""


# ── should_denoise ──

def test_should_denoise_true_for_short_requested():
    assert preflight.should_denoise(600.0, True) is True


def test_should_denoise_false_for_long_requested():
    assert preflight.should_denoise(46 * 60.0, True) is False


def test_should_denoise_true_at_exactly_threshold():
    assert preflight.should_denoise(45 * 60.0, True) is True


def test_should_denoise_false_when_not_requested():
    assert preflight.should_denoise(60.0, False) is False


def test_should_denoise_true_when_duration_unknown():
    assert preflight.should_denoise(None, True) is True


# ── estimate_cost ──

def test_estimate_cost_known_provider_one_hour():
    assert preflight.estimate_cost("AssemblyAI", 3600.0) == 0.17


def test_estimate_cost_none_when_duration_unknown():
    assert preflight.estimate_cost("AssemblyAI", None) is None


def test_estimate_cost_none_for_unknown_provider():
    assert preflight.estimate_cost("Nope", 3600.0) is None


# ── cost_hint_suffix ──

def test_cost_hint_suffix_formats_two_decimals():
    assert preflight.cost_hint_suffix("AssemblyAI", 3600.0) == " · ~$0.17"


def test_cost_hint_suffix_empty_when_duration_unknown():
    assert preflight.cost_hint_suffix("AssemblyAI", None) == ""


def test_cost_hint_suffix_empty_for_unknown_provider():
    assert preflight.cost_hint_suffix("Nope", 3600.0) == ""


def test_cost_hint_suffix_sub_cent_shows_less_than():
    # a 10 s AssemblyAI clip costs ~$0.0005 — show <$0.01, not a misleading $0.00
    assert preflight.cost_hint_suffix("AssemblyAI", 10.0) == " · ~<$0.01"
