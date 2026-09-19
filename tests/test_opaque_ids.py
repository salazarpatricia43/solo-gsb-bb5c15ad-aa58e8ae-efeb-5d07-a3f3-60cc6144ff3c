"""Opaque id read-back: URL-encoded event/participant ids in path params.

``POST /events`` accepts opaque ids (containing ``/``, ``%``, non-ASCII
characters, or spelled like a static route). These tests pin the read-back
contract: once URL-encoded, every legal id queries exactly the stored
record, literal static routes keep their behavior, and unknown ids still
answer 404.
"""

import pytest
from fastapi.testclient import TestClient

import app.db as db_module
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient backed by an isolated SQLite file."""
    monkeypatch.setattr(db_module, "DB_PATH", str(tmp_path / "opaque-id-test.db"))
    db_module.init_db()
    return TestClient(app)


def _post(client, event_id, participant_id, points, correction_of=None):
    payload = {
        "event_id": event_id,
        "participant_id": participant_id,
        "points": points,
    }
    if correction_of is not None:
        payload["correction_of"] = correction_of
    r = client.post("/events", json=payload)
    assert r.status_code == 201
    return r.json()


def test_event_id_with_slash_reads_back_via_encoded_path(client):
    ev = _post(client, "a/b", "alice", 10)
    r = client.get("/events/a%2Fb")
    assert r.status_code == 200
    assert r.json() == ev
    # the unencoded form keeps its old meaning: two segments, no route -> 404
    assert client.get("/events/a/b").status_code == 404


def test_event_id_with_multiple_slashes_and_segments(client):
    ev = _post(client, "x/y/z", "alice", 1)
    assert client.get("/events/x%2Fy%2Fz").json() == ev
    # a stored id never collides with a same-shaped encoded suffix
    assert client.get("/events/y%2Fz").status_code == 404


def test_event_named_like_static_route_reads_back_when_encoded(client):
    ev = _post(client, "changes", "alice", 3)
    # the literal path still belongs to the static route (422: missing params)
    assert client.get("/events/changes").status_code == 422
    # any percent-encoded character routes to the event lookup instead
    assert client.get("/events/%63hanges").json() == ev
    assert client.get("/events/%63%68%61%6e%67%65%73").json() == ev


def test_events_changes_static_route_behavior_unchanged(client):
    _post(client, "changes", "alice", 3)
    _post(client, "e2", "bob", 5)
    body = client.get(
        "/events/changes", params={"from_revision": 0, "to_revision": 2}
    ).json()
    assert [c["after"]["event_id"] for c in body["changes"]] == ["changes", "e2"]
    assert client.get("/events/changes").status_code == 422


def test_event_ids_shadowing_other_literal_names(client):
    # no GET route exists for these literals, so they were always readable;
    # lock the behavior in alongside the encoded forms.
    for event_id in ("batch", "preview"):
        ev = _post(client, event_id, "alice", 1)
        assert client.get(f"/events/{event_id}").json() == ev


def test_event_id_with_percent_and_space(client):
    ev = _post(client, "100%", "alice", 2)
    assert client.get("/events/100%25").json() == ev
    ev2 = _post(client, "a b", "alice", 4)
    assert client.get("/events/a%20b").json() == ev2


def test_utf8_event_id_reads_back(client):
    ev = _post(client, "比赛-1", "alice", 7)
    r = client.get("/events/%E6%AF%94%E8%B5%9B-1")
    assert r.status_code == 200
    assert r.json() == ev


def test_unknown_encoded_event_id_still_404(client):
    _post(client, "a/b", "alice", 10)
    assert client.get("/events/no%2Fsuch").status_code == 404
    assert client.get("/events/a%2Fc").status_code == 404
    assert client.get("/events/%63hanges").status_code == 404


def test_correction_chain_with_slash_ids(client):
    _post(client, "c/1", "alice", 10)
    ev2 = _post(client, "c/2", "alice", 12, correction_of="c/1")
    assert client.get("/events/c%2F2").json() == ev2
    lb = client.get("/leaderboard").json()
    alice = next(e for e in lb["entries"] if e["participant_id"] == "alice")
    assert [e["event_id"] for e in alice["effective_events"]] == ["c/2"]


def test_participant_with_slash_score_breakdown(client):
    _post(client, "e1", "p/1", 10)
    _post(client, "e2", "p/1", 5)
    _post(client, "e3", "p/12", 100)  # distinct id sharing the decoded prefix
    body = client.get("/participants/p%2F1/score-breakdown").json()
    assert body["total_points"] == 15
    assert [c["chain_root"] for c in body["contributions"]] == ["e1", "e2"]


def test_participant_with_slash_threshold_intervals(client):
    _post(client, "e1", "p/1", 10)
    _post(client, "e2", "p/1", 5)
    body = client.get(
        "/participants/p%2F1/threshold-intervals",
        params={"threshold": 10, "from_revision": 0, "to_revision": 2},
    ).json()
    assert body["participant_id"] == "p/1"
    assert body["intervals"] == [
        {"start_revision": 1, "end_revision": 2, "start_points": 10, "end_points": 15}
    ]


def test_participant_with_slash_leaderboard_proof(client):
    _post(client, "e1", "p/1", 10)
    _post(client, "e2", "alice", 5)
    proof = client.get("/leaderboard/proof/p%2F1").json()
    assert proof["entry"]["participant_id"] == "p/1"
    assert proof["root_hash"] == client.get("/leaderboard/commitment").json()["root_hash"]


def test_participant_named_like_static_route(client):
    _post(client, "e1", "compare", 9)
    # the literal static route is untouched ...
    r = client.get(
        "/participants/compare",
        params={"left_id": "compare", "right_id": "compare",
                "from_revision": 0, "to_revision": 1},
    )
    assert r.status_code == 200
    # ... and the participant routes address it as an ordinary id
    body = client.get("/participants/compare/score-breakdown").json()
    assert body["total_points"] == 9


def test_utf8_participant_id_reads_back(client):
    _post(client, "e1", "张三", 6)
    body = client.get("/participants/%E5%BC%A0%E4%B8%89/score-breakdown").json()
    assert body["total_points"] == 6
    proof = client.get("/leaderboard/proof/%E5%BC%A0%E4%B8%89").json()
    assert proof["entry"]["participant_id"] == "张三"


def test_unknown_encoded_participant_behaves_like_plain_unknown(client):
    _post(client, "e1", "alice", 10)
    # score-breakdown / threshold-intervals treat unknown participants as 0
    body = client.get("/participants/no%2Fbody/score-breakdown").json()
    assert body["total_points"] == 0
    assert body["contributions"] == []
    # the proof endpoint 404s for absent participants, encoded or not
    assert client.get("/leaderboard/proof/no%2Fbody").status_code == 404
