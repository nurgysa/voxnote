"""Provider-neutral Stage-0 ASR evaluation harness.

Pure logic only: no network calls, no VoxNote provider/UI imports, no config
or key reads. Consumes two JSON documents (a corpus manifest and a provider
result file) and produces a machine-readable report plus a Markdown summary.

Manifest JSON contract (schema_version 1)::

    {
      "schema_version": 1,
      "items": [
        {
          "id": "ru-clean-001",
          "gold_text": "...human-verified reference transcript...",
          "languages": ["ru"],
          "tags": ["clean"],
          "key_terms": ["optional", "domain terms"]
        }
      ]
    }

Result JSON contract (schema_version 1)::

    {
      "schema_version": 1,
      "provider": "assemblyai",
      "model": "universal-2",
      "items": [
        {
          "id": "ru-clean-001",
          "text": "...provider hypothesis...",
          "wall_clock_s": 12.3,
          "cost_usd": 0.01,
          "segments": [{"start": 0.0, "end": 1.2, "text": "..."}]
        }
      ]
    }
"""

import json
import re
import unicodedata

_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+", flags=re.UNICODE)

SCHEMA_VERSION = 1


def _validate_schema_version(doc, kind):
    if "schema_version" not in doc:
        raise ValueError(f"{kind} missing schema_version")
    version = doc["schema_version"]
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported {kind} schema_version: {version!r} (expected {SCHEMA_VERSION})"
        )


def _validate_items_list(doc, kind):
    items = doc.get("items")
    if not isinstance(items, list):
        raise ValueError(f"{kind} items must be a list")
    return items


def normalize_text(text):
    """Casefold, strip punctuation, and collapse whitespace for WER comparison."""
    text = unicodedata.normalize("NFKC", text)
    text = text.casefold()
    text = _PUNCT_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


def word_error_stats(reference, hypothesis):
    """Compute normalized word-level edit distance between reference and hypothesis.

    Uses the standard Levenshtein DP over words (substitution/deletion/insertion
    each cost 1) to derive S/D/I counts and WER = (S+D+I) / len(ref_words).
    """
    ref_words = normalize_text(reference).split()
    hyp_words = normalize_text(hypothesis).split()

    n, m = len(ref_words), len(hyp_words)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref_words[i - 1] == hyp_words[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(
                    dp[i - 1][j - 1],  # substitution
                    dp[i - 1][j],  # deletion
                    dp[i][j - 1],  # insertion
                )

    substitutions = 0
    deletions = 0
    insertions = 0
    i, j = n, m
    while i > 0 or j > 0:
        same = i > 0 and j > 0 and ref_words[i - 1] == hyp_words[j - 1]
        if same and dp[i][j] == dp[i - 1][j - 1]:
            i -= 1
            j -= 1
        elif i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + 1:
            substitutions += 1
            i -= 1
            j -= 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            deletions += 1
            i -= 1
        else:
            insertions += 1
            j -= 1

    wer = (substitutions + deletions + insertions) / n if n else 0.0
    return {
        "wer": wer,
        "substitutions": substitutions,
        "deletions": deletions,
        "insertions": insertions,
        "ref_word_count": n,
        "hyp_word_count": m,
    }


def key_term_recall(hypothesis, key_terms):
    """Fraction of unique normalized key terms present as substrings of the hypothesis."""
    padded_hyp = f" {normalize_text(hypothesis)} "
    unique_terms = sorted({normalize_text(term) for term in key_terms if term})

    found_terms = [term for term in unique_terms if term and f" {term} " in padded_hyp]
    missing_terms = [term for term in unique_terms if term not in found_terms]

    total = len(unique_terms)
    found = len(found_terms)
    return {
        "found": found,
        "total": total,
        "recall": (found / total) if total else 1.0,
        "missing": missing_terms,
    }


def load_manifest(path):
    """Load and validate a Stage-0 corpus manifest JSON file (schema_version 1)."""
    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)

    _validate_schema_version(manifest, "manifest")
    items = _validate_items_list(manifest, "manifest")

    seen_ids = set()
    for item in items:
        item_id = item.get("id", "")
        if not isinstance(item_id, str) or not item_id.strip():
            raise ValueError(f"manifest item has empty id: {item!r}")
        if item_id in seen_ids:
            raise ValueError(f"duplicate manifest item id: {item_id!r}")
        seen_ids.add(item_id)

        gold_text = item.get("gold_text", "")
        if not isinstance(gold_text, str) or not gold_text.strip():
            raise ValueError(f"manifest item {item_id!r} has empty gold_text")

    return manifest


def load_result(path, manifest):
    """Load and validate a provider result JSON file (schema_version 1) against a manifest.

    Every manifest item id must appear exactly once among the result items;
    result item ids not present in the manifest are rejected as unknown.
    """
    with open(path, encoding="utf-8") as f:
        result = json.load(f)

    _validate_schema_version(result, "result")
    items = _validate_items_list(result, "result")

    manifest_ids = {item["id"] for item in manifest["items"]}

    seen_ids = set()
    for item in items:
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id.strip():
            raise ValueError(f"result item has empty or non-string id: {item!r}")
        if item_id in seen_ids:
            raise ValueError(f"duplicate result item id: {item_id!r}")
        seen_ids.add(item_id)
        if item_id not in manifest_ids:
            raise ValueError(f"unknown result item id (not in manifest): {item_id!r}")

    missing_ids = manifest_ids - seen_ids
    if missing_ids:
        raise ValueError(f"result is missing manifest item ids: {sorted(missing_ids)!r}")

    return result


def build_report(manifest, result):
    """Aggregate WER, key-term recall, and segment/cost metrics into a report dict.

    Assumes ``manifest``/``result`` already passed `load_manifest`/`load_result`
    validation, so every manifest item id has exactly one matching result item.
    """
    items_by_id = {item["id"]: item for item in result["items"]}
    item_reports = []
    wers = []
    key_term_recalls = []
    segments_available_count = 0
    wall_clock_values = []
    cost_values = []

    for manifest_item in manifest["items"]:
        item_id = manifest_item["id"]
        result_item = items_by_id[item_id]
        hyp_text = result_item.get("text", "")
        stats = word_error_stats(manifest_item["gold_text"], hyp_text)
        wers.append(stats["wer"])

        key_terms = manifest_item.get("key_terms") or []
        recall = None
        if key_terms:
            recall = key_term_recall(hyp_text, key_terms)
            key_term_recalls.append(recall["recall"])

        segments = result_item.get("segments") or []
        segments_available = bool(segments)
        if segments_available:
            segments_available_count += 1

        wall_clock_s = result_item.get("wall_clock_s")
        if wall_clock_s is not None:
            wall_clock_values.append(wall_clock_s)
        cost_usd = result_item.get("cost_usd")
        if cost_usd is not None:
            cost_values.append(cost_usd)

        item_reports.append(
            {
                "id": item_id,
                "languages": manifest_item.get("languages", []),
                "tags": manifest_item.get("tags", []),
                "wer": stats["wer"],
                "substitutions": stats["substitutions"],
                "deletions": stats["deletions"],
                "insertions": stats["insertions"],
                "ref_word_count": stats["ref_word_count"],
                "hyp_word_count": stats["hyp_word_count"],
                "key_term_recall": recall,
                "segment_count": len(segments),
                "segments_available": segments_available,
                "wall_clock_s": wall_clock_s,
                "cost_usd": cost_usd,
            }
        )

    item_count = len(item_reports)
    mean_wer = sum(wers) / item_count if item_count else 0.0
    mean_key_term_recall = (
        sum(key_term_recalls) / len(key_term_recalls) if key_term_recalls else None
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "provider": result.get("provider"),
        "model": result.get("model"),
        "item_count": item_count,
        "mean_wer": mean_wer,
        "mean_key_term_recall": mean_key_term_recall,
        "segments_available_count": segments_available_count,
        "total_wall_clock_s": sum(wall_clock_values) if wall_clock_values else None,
        "total_cost_usd": sum(cost_values) if cost_values else None,
        "items": item_reports,
    }


def render_markdown(report):
    """Render a `build_report` result as a human-readable Markdown summary."""
    lines = [
        "# ASR Stage-0 Evaluation Report",
        "",
        f"- Provider: `{report.get('provider')}`",
        f"- Model: `{report.get('model')}`",
        f"- Items evaluated: {report['item_count']}",
        f"- Mean WER: {report['mean_wer']:.4f}",
    ]
    if report["mean_key_term_recall"] is not None:
        lines.append(f"- Mean key-term recall: {report['mean_key_term_recall']:.4f}")
    lines.append(
        f"- Segments available: {report['segments_available_count']}/{report['item_count']}"
    )
    if report["total_wall_clock_s"] is not None:
        lines.append(f"- Total wall-clock: {report['total_wall_clock_s']:.2f}s")
    if report["total_cost_usd"] is not None:
        lines.append(f"- Total cost: ${report['total_cost_usd']:.4f}")

    lines.append("")
    lines.append("| id | wer | key-term recall | segments | wall-clock (s) | cost (usd) |")
    lines.append("|---|---|---|---|---|---|")
    for item in report["items"]:
        recall = item["key_term_recall"]
        recall_str = f"{recall['recall']:.2f}" if recall else "—"
        wall_clock = item["wall_clock_s"]
        wall_clock_str = f"{wall_clock:.2f}" if wall_clock is not None else "—"
        cost = item["cost_usd"]
        cost_str = f"{cost:.4f}" if cost is not None else "—"
        lines.append(
            f"| {item['id']} | {item['wer']:.4f} | {recall_str} | "
            f"{item['segment_count']} | {wall_clock_str} | {cost_str} |"
        )
    lines.append("")

    return "\n".join(lines)
