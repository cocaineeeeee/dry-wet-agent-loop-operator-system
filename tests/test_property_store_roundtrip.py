"""P1 — event-stream round-trip property (M22 property-test batch).

Drives the REAL ``RunStore`` append/read path (expos/kernel/store.py): for an
arbitrary generated sequence of well-formed events (registered kinds carrying
their minimal valid payloads per ``EVENT_PAYLOAD_REQUIRED`` plus arbitrary
unicode/edge-value extra content), append via ``append_event`` then read back via
``read_events`` and prove the stream is preserved end to end:

  * COUNT — the number of events read equals the number appended;
  * ORDER — kinds and payloads come back in append order;
  * SEQ — the seq field is monotone and contiguous 0..N-1;
  * CONTENT — each event's payload is byte-for-byte the dict that was appended
    (JSON-native round-trip identity);
  * FILTER — ``read_events(kind=k)`` returns exactly the k-kinded subset in order;
  * HIGH-WATER — the tail-scan diagnostic (``scan_events_tail``) agrees with the
    full read: a clean append-only log reports ``status="clean"`` with
    ``n_lines == valid_up_to_line == N``.

This upgrades the fixed-input store round-trip regressions to arbitrary-input
proof. It writes only to a per-example temp dir under the scratch root and cleans
up; determinism is pinned with ``derandomize=True`` + ``database=None`` (the
official-docs-endorsed CI recipe: prefer a pinned ``@example`` over a machine
cache).
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import tempfile
from pathlib import Path

import pytest

if importlib.util.find_spec("hypothesis") is None:  # graceful skip w/o dev extra
    pytest.skip("hypothesis not installed (pip install -e '.[dev]')",
                allow_module_level=True)

from hypothesis import example, given, settings
from hypothesis import strategies as st

from expos.kernel.store import RunStore, StoreError

_SCRATCH = os.environ.get("PROPTEST_SCRATCH") or tempfile.gettempdir()

# ---------------------------------------------------------------------------
# REAL BUG found by this property (pinned strict-xfail below,
# ``test_unicode_line_separator_breaks_read_events``): the store's event log is
# LF-delimited JSONL, but ``read_events`` / ``_recover_next_seq`` split the file
# with ``str.splitlines()``, which treats U+0085 (NEL), U+2028 (LINE SEPARATOR)
# and U+2029 (PARAGRAPH SEPARATOR) as line boundaries — while ``json.dumps(...,
# ensure_ascii=False)`` (the writer) emits those three codepoints RAW (JSON only
# mandates escaping U+0000–U+001F). So an event whose payload text carries any of
# them is over-split into several physical "lines" on read and rejected/lost.
# The generic round-trip property below deliberately EXCLUDES these three
# codepoints from generated text so it stays a clean regression guard for the
# supported input space; the defect itself is pinned separately.
_LINE_BOUNDARY_CODEPOINTS = "\x85  "

# Text over the SUPPORTED input space: everything except the three raw-emitted
# line-boundary codepoints and surrogates (which cannot be UTF-8 encoded).
_SAFE_TEXT = st.text(
    st.characters(
        blacklist_characters=_LINE_BOUNDARY_CODEPOINTS,
        blacklist_categories=("Cs",),
    )
)

# JSON-native value space (what survives a json.dumps/json.loads round-trip as an
# identity): scalars + nested lists/dicts. NaN/inf are excluded on purpose — they
# would serialize to non-standard tokens and, for NaN, break value equality, which
# is a JSON-transport artifact, not a store round-trip defect.
_JSON_SCALARS = (
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | _SAFE_TEXT
)
_JSON_VALUES = st.recursive(
    _JSON_SCALARS,
    lambda children: st.lists(children, max_size=4)
    | st.dictionaries(
        st.text(
            st.characters(
                blacklist_characters=_LINE_BOUNDARY_CODEPOINTS,
                blacklist_categories=("Cs",),
            ),
            max_size=6,
        ),
        children,
        max_size=4,
    ),
    max_leaves=8,
)

# Registered kinds (a subset of EVENT_PAYLOAD_REQUIRED that is NOT dedup-guarded,
# so a plain append_event is the correct producer) -> a strategy for the minimal
# valid required payload. Extra arbitrary keys are merged on top per event.
_KIND_REQUIRED = {
    "routing": st.fixed_dictionaries({"obs_id": _SAFE_TEXT}),
    "action_consumed": st.fixed_dictionaries(
        {"item_uid": _SAFE_TEXT, "round_id": st.integers(min_value=0, max_value=64)}
    ),
    "redo_reconciliation": st.fixed_dictionaries(
        {"from_round": st.integers(min_value=0, max_value=64)}
    ),
    "run_stop": st.fixed_dictionaries(
        {"exit_status": st.sampled_from(["success", "error", "crash"])}
    ),
    "risk_map_applied": st.fixed_dictionaries(
        {"round_id": st.integers(min_value=0, max_value=64)}
    ),
    "aggregation_alpha": st.fixed_dictionaries(
        {"round_id": st.integers(min_value=0, max_value=64)}
    ),
    "reclassification": st.fixed_dictionaries(
        {"obs_id": _SAFE_TEXT, "to_trust": st.sampled_from(["trusted", "suspect"])}
    ),
    "learning_weight_assigned": st.fixed_dictionaries(
        {
            "round_id": st.integers(min_value=0, max_value=64),
            "entries": st.lists(_JSON_VALUES, max_size=3),
        }
    ),
}


@st.composite
def _event(draw) -> tuple[str, dict]:
    kind = draw(st.sampled_from(sorted(_KIND_REQUIRED)))
    required = draw(_KIND_REQUIRED[kind])
    extra = draw(
        st.dictionaries(
            st.text(
                st.characters(
                    blacklist_characters=_LINE_BOUNDARY_CODEPOINTS,
                    blacklist_categories=("Cs",),
                ),
                max_size=8,
            ),
            _JSON_VALUES,
            max_size=3,
        )
    )
    # required keys win over any colliding extra key so the payload is always valid.
    return kind, {**extra, **required}


def _fresh_store() -> tuple[RunStore, Path]:
    d = Path(tempfile.mkdtemp(prefix="p1_", dir=_SCRATCH))
    return RunStore(d / "run", create=True), d


@settings(max_examples=250, deadline=2000, derandomize=True, database=None)
@given(events=st.lists(_event(), max_size=25))
# Pinned edge examples (prefer @example over the machine cache, per the docs recipe):
# empty stream, and a unicode / edge-value payload (astral char, empty string,
# nested container, -0.0, huge int).
@example(events=[])
@example(
    events=[
        ("routing", {"obs_id": "\U0001f9ea", "ключ": "значение", "": []}),
        (
            "action_consumed",
            {"item_uid": "u❤️", "round_id": 0, "nested": {"a": [1, -0.0]}},
        ),
        ("run_stop", {"exit_status": "success", "big": 10**30, "flag": True}),
    ]
)
def test_event_stream_roundtrip(events):
    store, d = _fresh_store()
    try:
        appended = [store.append_event(kind, payload) for kind, payload in events]

        read = store.read_events()

        # COUNT
        assert len(read) == len(events)
        # SEQ — monotone contiguous 0..N-1
        assert [e["seq"] for e in read] == list(range(len(events)))
        # ORDER + CONTENT — kind and payload preserved position-for-position, and
        # the payload is the exact dict appended (JSON-native round-trip identity).
        for (kind, payload), got, appended_rec in zip(events, read, appended):
            assert got["kind"] == kind
            assert got["payload"] == payload
            assert got["seq"] == appended_rec["seq"]
        # FILTER — read_events(kind=k) is exactly the ordered k-subset.
        for kind in {k for k, _ in events}:
            want = [p for k, p in events if k == kind]
            got = [e["payload"] for e in store.read_events(kind=kind)]
            assert got == want

        # HIGH-WATER — the tail-scan diagnostic agrees with the full read: a clean
        # append-only log has status "clean" and its watermark line count equals N.
        scan = store.scan_events_tail()
        assert scan["status"] == "clean"
        assert scan["n_lines"] == len(events)
        assert scan["valid_up_to_line"] == len(events)
        assert scan["valid_up_to_byte"] == scan["size"]
        # scan_view_health rolls the tail scan into "events: healthy" (or missing
        # when the log was never created).
        health = store.scan_view_health()["sections"]["events"]["status"]
        assert health == ("healthy" if events else "missing")
    finally:
        shutil.rmtree(d, ignore_errors=True)


@settings(max_examples=150, deadline=2000, derandomize=True, database=None)
@given(events=st.lists(_event(), min_size=1, max_size=20))
def test_read_is_idempotent_and_reopen_stable(events):
    """Reading twice, and reopening the run dir with a fresh RunStore (a resume /
    new-process read handle), yields the identical event stream — reads never
    mutate the log and seq recovery is stable across handles."""
    store, d = _fresh_store()
    try:
        for kind, payload in events:
            store.append_event(kind, payload)
        first = store.read_events()
        second = store.read_events()
        assert first == second

        reopened = RunStore(d / "run", create=False)
        assert reopened.read_events() == first
        # A fresh handle's next seq continues contiguously from the recovered tail.
        rec = reopened.append_event("run_stop", {"exit_status": "success"})
        assert rec["seq"] == len(events)
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ============================================================ REAL BUG (strict-xfail pin)


@pytest.mark.parametrize("sep", ["\x85", " ", " "])
def test_unicode_line_separator_roundtrips(sep, tmp_path):
    """Regression pin for the P1-found line-boundary bug (FIXED; was strict-xfail).

    Reader fix: split on the LF byte only (store.py read_events/_recover_next_seq).
    Kill-verification: reverting either split site to str.splitlines() turns this red.

    CAUSAL ANALYSIS
    ---------------
    * WRITE (store.py ``append_event``): a record is
      ``json.dumps(record, ensure_ascii=False, default=str) + "\\n"``. JSON only
      mandates escaping U+0000–U+001F, so the C1 control char U+0085 (NEL) and the
      Unicode separators U+2028 (LINE SEPARATOR) / U+2029 (PARAGRAPH SEPARATOR)
      are emitted as RAW bytes inside the JSON string body. The only record
      delimiter written is a single LF.
    * READ (store.py ``read_events`` / ``_recover_next_seq``):
      ``p.read_text(...).splitlines()`` splits on the FULL Unicode line-boundary
      set — which INCLUDES U+0085/U+2028/U+2029. So one logical event is split
      into multiple physical "lines"; the fragment before the separator is invalid
      JSON and, not being the physical last line, raises ``StoreError``
      ("middle line corrupt"). At the tail it is instead silently swallowed as a
      torn-tail — silent data loss.
    * INCONSISTENCY: ``scan_events_tail`` splits at the BYTE level
      (``data.find(b"\\n")``) so it reports the SAME file ``status="clean"`` while
      ``read_events`` rejects it — the ``expos check`` health surface and the
      reader disagree on identical bytes.

    REACHABILITY: any event payload text field (``obs_id``, a claim ``statement``,
    an LLM agent proposal, a routing key) can carry U+2028/U+2029 (ubiquitous in
    JS/LLM/scraped text) or U+0085 — then ``read_events`` DoS-raises on
    status/verdicts/``--resume``, or drops the tail event silently.

    FIX DIRECTION (out of scope for this TESTS-ONLY batch): split the event log on
    the LF byte only (``read_text().split("\\n")`` / byte-level split), matching
    the writer's delimiter and ``scan_events_tail``. This test is a strict-xfail so
    it turns red (xpass) the moment the reader is fixed — the un-pin signal.
    """
    d = Path(tempfile.mkdtemp(prefix="p1_bug_", dir=_SCRATCH))
    try:
        store = RunStore(d / "run", create=True)
        store.append_event("routing", {"obs_id": f"a{sep}b"})
        store.append_event("routing", {"obs_id": "tail"})
        # The health surface calls the file clean (byte-level LF split)...
        assert store.scan_events_tail()["status"] == "clean"
        # ...but read_events (str.splitlines) over-splits and raises. DESIRED
        # behavior (post-fix) is a faithful 2-event round-trip preserving the
        # separator; until then this raises StoreError -> xfail(strict).
        events = store.read_events()
        assert [e["payload"]["obs_id"] for e in events] == [f"a{sep}b", "tail"]
    finally:
        shutil.rmtree(d, ignore_errors=True)
