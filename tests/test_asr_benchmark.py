import json

import pytest

from evaluation.asr_benchmark import (
    build_report,
    key_term_recall,
    load_manifest,
    load_result,
    normalize_text,
    render_markdown,
    word_error_stats,
)


def _write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_normalize_text_casefolds_and_strips_punctuation():
    assert normalize_text("Privet, MIR!") == "privet mir"


def test_word_error_stats_perfect_match_is_zero_wer():
    stats = word_error_stats("hello world", "hello world")
    assert stats["wer"] == 0.0
    assert stats["substitutions"] == 0
    assert stats["deletions"] == 0
    assert stats["insertions"] == 0
    assert stats["ref_word_count"] == 2
    assert stats["hyp_word_count"] == 2


def test_word_error_stats_counts_substitution_deletion_insertion():
    # ref: "the quick brown fox jumps" (5 words)
    # hyp: "the quick brownish fox jumps today" (6 words)
    # -> 1 substitution (brown -> brownish), 1 insertion (today), 0 deletions
    stats = word_error_stats(
        "the quick brown fox jumps",
        "the quick brownish fox jumps today",
    )
    assert stats["substitutions"] == 1
    assert stats["deletions"] == 0
    assert stats["insertions"] == 1
    assert stats["ref_word_count"] == 5
    assert stats["hyp_word_count"] == 6
    assert stats["wer"] == pytest.approx(2 / 5)


def test_normalize_text_handles_cyrillic_and_latin_punctuation():
    text = "Привет — «мир»!.. Hello, WORLD?!"
    assert normalize_text(text) == "привет мир hello world"


def test_key_term_recall_counts_unique_normalized_terms_found():
    result = key_term_recall(
        hypothesis="We discussed the Roadmap and the roadmap again, plus Budget.",
        key_terms=["Roadmap", "budget", "Timeline"],
    )
    assert result["found"] == 2
    assert result["total"] == 3
    assert result["recall"] == pytest.approx(2 / 3)
    assert result["missing"] == ["timeline"]


def test_key_term_recall_does_not_match_partial_words():
    result = key_term_recall(hypothesis="We started the project", key_terms=["art"])
    assert result["found"] == 0
    assert result["missing"] == ["art"]


def test_load_manifest_parses_valid_file(tmp_path):
    manifest_path = _write_json(
        tmp_path / "manifest.json",
        {
            "schema_version": 1,
            "items": [
                {
                    "id": "ru-clean-001",
                    "gold_text": "Привет мир",
                    "languages": ["ru"],
                    "tags": ["clean"],
                }
            ],
        },
    )

    manifest = load_manifest(manifest_path)

    assert manifest["schema_version"] == 1
    assert manifest["items"][0]["id"] == "ru-clean-001"


def test_load_manifest_rejects_duplicate_ids(tmp_path):
    manifest_path = _write_json(
        tmp_path / "manifest.json",
        {
            "schema_version": 1,
            "items": [
                {"id": "dup", "gold_text": "a", "languages": ["en"], "tags": []},
                {"id": "dup", "gold_text": "b", "languages": ["en"], "tags": []},
            ],
        },
    )

    with pytest.raises(ValueError, match="duplicate"):
        load_manifest(manifest_path)


def test_load_manifest_rejects_empty_gold_text(tmp_path):
    manifest_path = _write_json(
        tmp_path / "manifest.json",
        {
            "schema_version": 1,
            "items": [
                {"id": "a", "gold_text": "   ", "languages": ["en"], "tags": []},
            ],
        },
    )

    with pytest.raises(ValueError, match="gold_text"):
        load_manifest(manifest_path)


def test_load_manifest_rejects_empty_id(tmp_path):
    manifest_path = _write_json(
        tmp_path / "manifest.json",
        {
            "schema_version": 1,
            "items": [
                {"id": "  ", "gold_text": "a", "languages": ["en"], "tags": []},
            ],
        },
    )

    with pytest.raises(ValueError, match="id"):
        load_manifest(manifest_path)


def _sample_manifest():
    return {
        "schema_version": 1,
        "items": [
            {"id": "a", "gold_text": "hello world", "languages": ["en"], "tags": []},
            {"id": "b", "gold_text": "goodnight moon", "languages": ["en"], "tags": []},
        ],
    }


def test_load_result_parses_valid_file(tmp_path):
    result_path = _write_json(
        tmp_path / "result.json",
        {
            "schema_version": 1,
            "provider": "groq",
            "model": "whisper-large-v3",
            "items": [
                {"id": "a", "text": "hello world"},
                {"id": "b", "text": "goodnight moon"},
            ],
        },
    )

    result = load_result(result_path, _sample_manifest())

    assert result["provider"] == "groq"
    assert result["items"][0]["id"] == "a"


def test_load_result_rejects_missing_manifest_id(tmp_path):
    result_path = _write_json(
        tmp_path / "result.json",
        {
            "schema_version": 1,
            "provider": "groq",
            "model": "whisper-large-v3",
            "items": [{"id": "a", "text": "hello world"}],
        },
    )

    with pytest.raises(ValueError, match="missing"):
        load_result(result_path, _sample_manifest())


def test_load_result_rejects_duplicate_id(tmp_path):
    result_path = _write_json(
        tmp_path / "result.json",
        {
            "schema_version": 1,
            "provider": "groq",
            "model": "whisper-large-v3",
            "items": [
                {"id": "a", "text": "hello world"},
                {"id": "a", "text": "hello world again"},
                {"id": "b", "text": "goodnight moon"},
            ],
        },
    )

    with pytest.raises(ValueError, match="duplicate"):
        load_result(result_path, _sample_manifest())


def test_load_result_rejects_unknown_id(tmp_path):
    result_path = _write_json(
        tmp_path / "result.json",
        {
            "schema_version": 1,
            "provider": "groq",
            "model": "whisper-large-v3",
            "items": [
                {"id": "a", "text": "hello world"},
                {"id": "b", "text": "goodnight moon"},
                {"id": "z", "text": "unknown item"},
            ],
        },
    )

    with pytest.raises(ValueError, match="unknown"):
        load_result(result_path, _sample_manifest())


def test_load_manifest_rejects_missing_schema_version(tmp_path):
    manifest_path = _write_json(
        tmp_path / "manifest.json",
        {"items": [{"id": "a", "gold_text": "hi", "languages": ["en"], "tags": []}]},
    )

    with pytest.raises(ValueError, match="schema_version"):
        load_manifest(manifest_path)


def test_load_manifest_rejects_unsupported_schema_version(tmp_path):
    manifest_path = _write_json(
        tmp_path / "manifest.json",
        {
            "schema_version": 2,
            "items": [{"id": "a", "gold_text": "hi", "languages": ["en"], "tags": []}],
        },
    )

    with pytest.raises(ValueError, match="schema_version"):
        load_manifest(manifest_path)


def test_load_manifest_rejects_non_list_items(tmp_path):
    manifest_path = _write_json(tmp_path / "manifest.json", {"schema_version": 1, "items": "nope"})

    with pytest.raises(ValueError, match="items"):
        load_manifest(manifest_path)


def test_load_result_rejects_missing_schema_version(tmp_path):
    result_path = _write_json(
        tmp_path / "result.json",
        {
            "provider": "groq",
            "model": "whisper-large-v3",
            "items": [{"id": "a", "text": "hello world"}, {"id": "b", "text": "goodnight moon"}],
        },
    )

    with pytest.raises(ValueError, match="schema_version"):
        load_result(result_path, _sample_manifest())


def test_load_result_rejects_unsupported_schema_version(tmp_path):
    result_path = _write_json(
        tmp_path / "result.json",
        {
            "schema_version": 99,
            "provider": "groq",
            "model": "whisper-large-v3",
            "items": [{"id": "a", "text": "hello world"}, {"id": "b", "text": "goodnight moon"}],
        },
    )

    with pytest.raises(ValueError, match="schema_version"):
        load_result(result_path, _sample_manifest())


def test_load_result_rejects_item_with_non_string_id(tmp_path):
    result_path = _write_json(
        tmp_path / "result.json",
        {
            "schema_version": 1,
            "provider": "groq",
            "model": "whisper-large-v3",
            "items": [{"id": 1, "text": "hello world"}, {"id": "b", "text": "goodnight moon"}],
        },
    )

    with pytest.raises(ValueError, match="id"):
        load_result(result_path, _sample_manifest())


def _sample_result():
    return {
        "schema_version": 1,
        "provider": "groq",
        "model": "whisper-large-v3",
        "items": [
            {
                "id": "a",
                "text": "hello world",
                "wall_clock_s": 1.5,
                "cost_usd": 0.02,
                "segments": [{"start": 0.0, "end": 1.0, "text": "hello world"}],
            },
            {"id": "b", "text": "goodnight moons today"},
        ],
    }


def _sample_manifest_with_key_terms():
    manifest = _sample_manifest()
    manifest["items"][0]["key_terms"] = ["hello"]
    return manifest


def test_build_report_computes_per_item_and_aggregate_metrics():
    report = build_report(_sample_manifest_with_key_terms(), _sample_result())

    assert report["schema_version"] == 1
    assert report["provider"] == "groq"
    assert report["model"] == "whisper-large-v3"
    assert report["item_count"] == 2

    item_a = report["items"][0]
    assert item_a["id"] == "a"
    assert item_a["wer"] == 0.0
    assert item_a["key_term_recall"]["recall"] == 1.0
    assert item_a["segment_count"] == 1
    assert item_a["segments_available"] is True
    assert item_a["wall_clock_s"] == 1.5
    assert item_a["cost_usd"] == 0.02

    item_b = report["items"][1]
    assert item_b["id"] == "b"
    assert item_b["key_term_recall"] is None
    assert item_b["segment_count"] == 0
    assert item_b["segments_available"] is False
    assert item_b["wall_clock_s"] is None
    assert item_b["cost_usd"] is None

    # item b: ref "goodnight moon" (2 words) vs hyp "goodnight moons today"
    # -> 1 substitution (moon -> moons), 1 insertion (today)
    assert item_b["wer"] == pytest.approx(1.0)

    assert report["mean_wer"] == pytest.approx((0.0 + 1.0) / 2)
    assert report["mean_key_term_recall"] == pytest.approx(1.0)
    assert report["segments_available_count"] == 1
    assert report["total_wall_clock_s"] == pytest.approx(1.5)
    assert report["total_cost_usd"] == pytest.approx(0.02)


def test_build_report_omits_totals_when_no_item_reports_cost_or_wall_clock():
    manifest = _sample_manifest()
    result = {
        "schema_version": 1,
        "provider": "groq",
        "model": "whisper-large-v3",
        "items": [{"id": "a", "text": "hello world"}, {"id": "b", "text": "goodnight moon"}],
    }

    report = build_report(manifest, result)

    assert report["total_wall_clock_s"] is None
    assert report["total_cost_usd"] is None
    assert report["mean_key_term_recall"] is None
    assert report["segments_available_count"] == 0


def test_render_markdown_includes_summary_and_per_item_table():
    report = build_report(_sample_manifest_with_key_terms(), _sample_result())

    markdown = render_markdown(report)

    assert "# ASR Stage-0 Evaluation Report" in markdown
    assert "groq" in markdown
    assert "whisper-large-v3" in markdown
    assert "Items evaluated: 2" in markdown
    assert "| a |" in markdown
    assert "| b |" in markdown
    assert "Total wall-clock" in markdown
    assert "Total cost" in markdown


def test_render_markdown_handles_missing_optional_totals():
    manifest = _sample_manifest()
    result = {
        "schema_version": 1,
        "provider": "groq",
        "model": "whisper-large-v3",
        "items": [{"id": "a", "text": "hello world"}, {"id": "b", "text": "goodnight moon"}],
    }
    report = build_report(manifest, result)

    markdown = render_markdown(report)

    assert "Total wall-clock" not in markdown
    assert "Total cost" not in markdown
    assert "—" in markdown
