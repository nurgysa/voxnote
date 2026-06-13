#!/usr/bin/env python3
"""Real-API smoke for live-board dedup (Task 13, CLI variant).

Mocked unit tests prove our LOGIC; they cannot prove the CONTRACT with the
live Linear GraphQL / Trello REST APIs (the class of bug that false-greened
Trello in PR #79). This script exercises the *real* production code paths
against the *real* APIs, using the keys from a config.json, so a green run
means the query/endpoint shapes are actually accepted upstream.

It imports the shipped backends rather than reimplementing the calls — so it
can only pass if LinearBackend/TrelloBackend + their clients work for real.

READ-ONLY by default: calls bootstrap (discover containers), list_existing
(list_issues / list_open_cards) and comment_exists (list_comments /
list_card_comments). It posts NOTHING unless you opt in with --post-backend +
--post-ref, which runs the full idempotency round-trip on the ONE ref you name.

Usage (from repo root):
    python scripts/smoke_dedup_live.py
    python scripts/smoke_dedup_live.py --linear-team-key NUR
    python scripts/smoke_dedup_live.py --config <path-to-config.json>
    python scripts/smoke_dedup_live.py --post-backend linear --post-ref <issue-uuid>

Exit code 0 = all attempted checks passed; 1 = at least one failed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Make the repo root importable when run as `python scripts/smoke_dedup_live.py`.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tasks.backends.linear import LinearBackend  # noqa: E402
from tasks.backends.trello import TrelloBackend  # noqa: E402
from tasks.dedup import dedup_marker  # noqa: E402
from tasks.linear_client import LinearClient  # noqa: E402
from tasks.trello_client import TrelloClient  # noqa: E402

OK = "[OK] "
FAIL = "[FAIL] "
SKIP = "[skip] "
_PROBE = "__audiotx_smoke_probe_no_match__"


def _mask(secret: str) -> str:
    """Show only length + last 4 chars of a secret (never the full key)."""
    if not secret:
        return "EMPTY"
    return f"set (len {len(secret)}, ...{secret[-4:]})"


def _close(client) -> None:
    # best-effort cleanup; a failed close must not fail the smoke
    try:
        client.close()
    except Exception:
        pass


def _discover_config(explicit: str | None) -> str:
    """Resolve which config.json to read: explicit, then C:\\Apps, then repo."""
    candidates = [
        explicit,
        r"C:\Apps\AudioTranscriber\_internal\config.json",
        os.path.join(_REPO_ROOT, "config.json"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    tried = ", ".join(c for c in candidates if c)
    raise SystemExit(f"{FAIL}No config.json found. Pass --config <path>. Tried: {tried}")


def _pick_container(containers, *, by_id, by_key, by_name, kind):
    """Choose one Container from bootstrap(); print the menu if ambiguous.

    Precedence: id -> key (Linear) -> name substring -> single -> first.
    Returns the chosen Container, or None when a selector matched nothing.
    """
    if not containers:
        return None
    if by_id:
        match = next((c for c in containers if c.id == by_id), None)
        if not match:
            print(f"  {FAIL}{kind}: no container with id={by_id}")
        return match
    if by_key:
        wanted = by_key.upper()
        match = next(
            (c for c in containers if (getattr(c, "key", None) or "").upper() == wanted),
            None,
        )
        if not match:
            print(f"  {FAIL}{kind}: no container with key={by_key}")
        return match
    if by_name:
        matches = [c for c in containers if by_name.lower() in c.name.lower()]
        if len(matches) == 1:
            return matches[0]
        word = "no" if not matches else f"{len(matches)} ambiguous"
        print(f"  {FAIL}{kind}: '{by_name}' -> {word} matches")
        return None
    if len(containers) == 1:
        return containers[0]
    print(f"  {kind}: {len(containers)} found — defaulting to first. Override flags:")
    for c in containers[:25]:
        key = f" key={c.key}" if getattr(c, "key", None) else ""
        print(f"    - id={c.id}{key} name={c.name!r}")
    return containers[0]


def _report_existing(items, kind: str) -> None:
    """Print a list_existing() result."""
    print(f"  {OK}list_existing OK — {len(items)} active {kind}")
    for it in items[:5]:
        print(f"      [{it.identifier}] {it.title[:60]!r} ref={(it.ref or '')[:10]}...")
    if len(items) > 5:
        print(f"      ... and {len(items) - 5} more")


def _check_comment_exists(backend, ref, kind_query: str) -> bool:
    """Probe comment_exists with a marker that cannot exist; expect False."""
    probe = dedup_marker(_PROBE)
    if backend.comment_exists(ref, probe):
        print(f"  {FAIL}comment_exists returned True for an impossible probe")
        return False
    print(f"  {OK}comment_exists OK — {kind_query} works (probe correctly absent)")
    return True


def smoke_linear(cfg: dict, args) -> bool | None:
    """Read-only Linear contract check. True/False, or None if skipped."""
    key = (cfg.get("linear_api_key") or "").strip()
    print("\n=== Linear ===")
    if not key:
        print(f"  {SKIP}linear_api_key empty — skipping Linear")
        return None
    print(f"  api key: {_mask(key)}")
    client = LinearClient(api_key=key)
    backend = LinearBackend(client)
    try:
        teams = backend.bootstrap()  # validates auth + list_teams query
        print(f"  {OK}bootstrap OK — {len(teams)} team(s) visible")
        team = _pick_container(
            teams, by_id=args.linear_team, by_key=args.linear_team_key,
            by_name=None, kind="Linear team",
        )
        if team is None:
            return False
        print(f"  using team: {team.name} (key={team.key}) id={team.id}")
        items = backend.list_existing(team.id)  # list_issues (paginated, active-only)
        _report_existing(items, "issues")
        if not items:
            print(f"  {SKIP}no active issues -> comment_exists not exercised")
            return True
        return _check_comment_exists(backend, items[0].ref, "list_comments query")
    except Exception as e:  # smoke must surface ANY failure as a FAIL line
        print(f"  {FAIL}Linear contract FAILED: {type(e).__name__}: {e}")
        return False
    finally:
        _close(client)


def smoke_trello(cfg: dict, args) -> bool | None:
    """Read-only Trello contract check. True/False, or None if skipped."""
    key = (cfg.get("trello_api_key") or "").strip()
    token = (cfg.get("trello_token") or "").strip()
    print("\n=== Trello ===")
    if not key or not token:
        print(f"  {SKIP}trello_api_key/token empty — skipping Trello")
        return None
    print(f"  api key: {_mask(key)}  token: {_mask(token)}")
    client = TrelloClient(api_key=key, token=token)
    backend = TrelloBackend(client)
    try:
        lists = backend.bootstrap()  # validates auth + list_containers
        print(f"  {OK}bootstrap OK — {len(lists)} list(s) visible")
        lst = _pick_container(
            lists, by_id=args.trello_list, by_key=None,
            by_name=args.trello_list_name, kind="Trello list",
        )
        if lst is None:
            return False
        print(f"  using list: {lst.name} id={lst.id}")
        items = backend.list_existing(lst.id)  # list_open_cards (BOARD-level)
        _report_existing(items, "cards")
        if not items:
            print(f"  {SKIP}no open cards -> comment_exists not exercised")
            return True
        return _check_comment_exists(backend, items[0].ref, "list_card_comments query")
    except Exception as e:  # smoke must surface ANY failure as a FAIL line
        print(f"  {FAIL}Trello contract FAILED: {type(e).__name__}: {e}")
        return False
    finally:
        _close(client)


def post_round_trip(cfg: dict, args) -> bool:
    """OPT-IN write test: prove idempotency on ONE ref the user names.

    Mirrors sender.py: marker = dedup_marker(title); if comment_exists -> skip;
    else add_comment(body w/ marker). Asserts absent -> post -> present. Posts
    exactly ONE real comment, only on the ref you pass.
    """
    name = args.post_backend
    ref = args.post_ref
    marker = dedup_marker(args.post_title)
    print(f"\n=== --post round-trip ({name}, ref={ref[:12]}...) ===")
    print(f"  marker for title {args.post_title!r}: {marker}")
    body = f"[smoke] dedup idempotency test ({args.post_title}).\n\n{marker}"
    if name == "linear":
        client = LinearClient(api_key=(cfg.get("linear_api_key") or "").strip())
        backend = LinearBackend(client)
    else:
        client = TrelloClient(
            api_key=(cfg.get("trello_api_key") or "").strip(),
            token=(cfg.get("trello_token") or "").strip(),
        )
        backend = TrelloBackend(client)
    try:
        before = backend.comment_exists(ref, marker)
        print(f"  comment_exists before: {before} (expect False on a fresh ref)")
        if before:
            print(f"  {OK}marker already present -> sender would SKIP. No post made.")
            return True
        backend.add_comment(ref, body)
        print(f"  {OK}add_comment posted one comment carrying the marker")
        after = backend.comment_exists(ref, marker)
        print(f"  comment_exists after: {after} (expect True)")
        if not after:
            print(f"  {FAIL}marker absent after posting — round-trip broken")
            return False
        print(f"  {OK}round-trip verified: a re-run would find the marker and SKIP")
        return True
    except Exception as e:  # smoke must surface ANY failure as a FAIL line
        print(f"  {FAIL}post round-trip FAILED: {type(e).__name__}: {e}")
        return False
    finally:
        _close(client)


def main() -> int:
    ap = argparse.ArgumentParser(description="Real-API smoke for live-board dedup.")
    ap.add_argument("--config", help="config.json path (default: auto C:\\Apps then repo)")
    ap.add_argument("--linear-team", help="Linear team id (default: by-key or first)")
    ap.add_argument("--linear-team-key", help="Linear team KEY, e.g. NUR")
    ap.add_argument("--trello-list", help="Trello list id (default: by-name or first)")
    ap.add_argument("--trello-list-name", help="Trello list name substring")
    ap.add_argument("--only", choices=["linear", "trello"], help="Run only one backend")
    ap.add_argument("--post-backend", choices=["linear", "trello"], help="Opt-in write test")
    ap.add_argument("--post-ref", help="Issue UUID / card id for the --post comment")
    ap.add_argument("--post-title", default="audiotx smoke", help="Title for the --post marker")
    args = ap.parse_args()

    cfg_path = _discover_config(args.config)
    print(f"config: {cfg_path}")
    with open(cfg_path, encoding="utf-8") as fh:
        cfg = json.load(fh)

    results: dict[str, bool | None] = {}
    if args.only != "trello":
        results["linear"] = smoke_linear(cfg, args)
    if args.only != "linear":
        results["trello"] = smoke_trello(cfg, args)
    if args.post_backend:
        if not args.post_ref:
            print(f"\n{FAIL}--post-backend requires --post-ref <id> (pick a throwaway)")
            return 1
        results["post"] = post_round_trip(cfg, args)

    print("\n=== summary ===")
    labels = {True: OK + "PASS", False: FAIL + "FAIL", None: SKIP + "SKIPPED"}
    failed = any(r is False for r in results.values())
    for nm, res in results.items():
        print(f"  {nm}: {labels[res]}")
    verdict = "FAIL — see [FAIL] lines" if failed else "PASS — API contract verified"
    print(f"\nRESULT: {verdict}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
