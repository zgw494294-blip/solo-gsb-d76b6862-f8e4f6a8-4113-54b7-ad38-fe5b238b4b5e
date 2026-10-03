"""迁移 / 导出接口的 HTTP 回归（FastAPI TestClient）。

重点覆盖**整数 id 与字符串 id 混同**的修复：同批包含 1 与 "1" 且均待
确认时——

- 旧格式 ``decisions["1"]`` 会同时对应两条输入批注 → **422**，不猜测；
- 新格式 ``decision_list``（数组，每条带原 id 与类型）分别定位：只处理
  一条后另一条仍待确认，导出 **409**；两条分别处理后才允许导出；
- 非法候选仍 **422**，同类型重复 id 在入口校验处仍 **422**；
- 旧格式键唯一对应一个输入 id 时，响应、顺序与状态码保持原行为。
"""

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("COPY_DB_PATH", str(tmp_path / "copies.db"))
    import app.main as main
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c
    main._store = None


def _mixed_payload(**overrides):
    """构造整数 id 1（并列两候选）与字符串 id "1"（无命中）均待确认的批次。"""
    block = "起" * 20 + "目标文本" + "止" * 20
    tail = "这段文字在新版中被整体删除。"
    old_text = block + "间隔" + block + "旧位：目标文本。\n" + tail
    new_text = block + "间隔" + block + "旧位：已改写。\n全新的内容。"
    # 旧版「旧位：目标文本。」中第三处“目标文本”：新版两处命中且上下文并列
    tie_start = old_text.rfind("目标文本")
    tail_start = tie_start + 4 + 2  # 跳过「目标文本。\n」
    payload = {
        "old_text": old_text,
        "new_text": new_text,
        "annotations": [
            {"id": 1, "start": tie_start, "end": tie_start + 4},
            {"id": "1", "start": tail_start, "end": tail_start + len(tail)},
        ],
    }
    payload.update(overrides)
    return payload


def _both_pending(client, payload):
    r = client.post("/api/migrate", json=payload)
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert [a["id"] for a in results] == [1, "1"]
    assert [type(a["id"]).__name__ for a in results] == ["int", "str"]
    assert all(a["status"] == "pending" for a in results)
    return results


# ---------- POST /api/migrate：保留 id 及类型，合法组合不拒收 ----------

def test_migrate_keeps_int_and_string_id_types(client):
    payload = _mixed_payload()
    results = _both_pending(client, payload)
    assert len(results[0]["candidates"]) == 2
    assert results[1]["candidates"] == []


def test_migrate_response_unchanged_for_simple_case(client):
    r = client.post("/api/migrate", json={
        "old_text": "第一段：旧版说明。第二段：保持不变。",
        "new_text": "第一段：全新的新版说明。第二段：保持不变。",
        "annotations": [{"id": "a1", "start": 13, "end": 17}],
    })
    assert r.status_code == 200
    item = r.json()["results"][0]
    assert item["status"] == "shifted"
    assert (item["new_start"], item["new_end"]) == (16, 20)


# ---------- 无决策：409，pending_ids 保留类型 ----------

def test_export_conflict_lists_pending_ids_with_types(client):
    payload = _mixed_payload()
    r = client.post("/api/export", json=payload)
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["message"] == "存在未处理的待确认批注，禁止导出"
    assert detail["pending_ids"] == [1, "1"]


# ---------- 旧格式对象键：歧义 → 422；唯一 → 原行为 ----------

def test_legacy_decisions_ambiguous_key_returns_422(client):
    payload = _mixed_payload(decisions={"1": {"action": "abandon"}})
    r = client.post("/api/export", json=payload)
    assert r.status_code == 422
    assert "多个不同类型" in r.json()["detail"]


def test_legacy_decisions_unique_key_keeps_behavior(client):
    # 只有字符串 id "1"：旧键唯一对应，废弃并导出
    r = client.post("/api/export", json={
        "old_text": "这段文字在新版中被整体删除。",
        "new_text": "全新的内容。",
        "annotations": [{"id": "1", "start": 0, "end": 6}],
        "decisions": {"1": {"action": "abandon"}},
    })
    assert r.status_code == 200, r.text
    assert r.json() == {"annotations": [
        {"id": "1", "start": None, "end": None, "status": "abandoned"}]}

    # 只有整数 id 1：旧键 "1" 仍唯一命中它（原行为）
    r = client.post("/api/export", json={
        "old_text": "这段文字在新版中被整体删除。",
        "new_text": "全新的内容。",
        "annotations": [{"id": 1, "start": 0, "end": 6}],
        "decisions": {"1": {"action": "abandon"}},
    })
    assert r.status_code == 200
    assert r.json() == {"annotations": [
        {"id": 1, "start": None, "end": None, "status": "abandoned"}]}


# ---------- 新格式 decision_list：按类型与值分别定位 ----------

def test_decision_list_only_int_processed_string_still_pending(client):
    payload = _mixed_payload(decisions=[{"id": 1, "action": "abandon"}])
    r = client.post("/api/export", json=payload)
    assert r.status_code == 409
    assert r.json()["detail"]["pending_ids"] == ["1"]


def test_decision_list_only_string_processed_int_still_pending(client):
    payload = _mixed_payload(decisions=[{"id": "1", "action": "abandon"}])
    r = client.post("/api/export", json=payload)
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["pending_ids"] == [1]
    assert type(detail["pending_ids"][0]) is int


def test_decision_list_both_processed_exports_in_order(client):
    results = _both_pending(client, _mixed_payload())
    cand = results[0]["candidates"][0]
    payload = _mixed_payload(decisions=[
        {"id": 1, "action": "choose", "start": cand["start"], "end": cand["end"]},
        {"id": "1", "action": "abandon"},
    ])
    r = client.post("/api/export", json=payload)
    assert r.status_code == 200, r.text
    assert r.json() == {"annotations": [
        {"id": 1, "start": cand["start"], "end": cand["end"], "status": "manual"},
        {"id": "1", "start": None, "end": None, "status": "abandoned"},
    ]}


def test_decision_list_unknown_id_returns_422(client):
    payload = _mixed_payload(decisions=[{"id": 2, "action": "abandon"}])
    r = client.post("/api/export", json=payload)
    assert r.status_code == 422
    assert "不存在" in r.json()["detail"]

    # 字符串 "1" 存在但整数 1 不在批次内
    r = client.post("/api/export", json={
        "old_text": "这段文字在新版中被整体删除。",
        "new_text": "全新的内容。",
        "annotations": [{"id": "1", "start": 0, "end": 6}],
        "decisions": [{"id": 1, "action": "abandon"}],
    })
    assert r.status_code == 422


def test_decision_list_duplicate_same_id_returns_422(client):
    payload = _mixed_payload(decisions=[
        {"id": 1, "action": "abandon"},
        {"id": 1, "action": "abandon"},
    ])
    r = client.post("/api/export", json=payload)
    assert r.status_code == 422
    assert "多条决策" in r.json()["detail"]


def test_decision_list_invalid_candidate_returns_422(client):
    payload = _mixed_payload(decisions=[
        {"id": 1, "action": "choose", "start": 0, "end": 4},
    ])
    r = client.post("/api/export", json=payload)
    assert r.status_code == 422
    assert "不在候选列表中" in r.json()["detail"]
    # 非法动作同样 422
    payload = _mixed_payload(decisions=[{"id": "1", "action": "wat"}])
    r = client.post("/api/export", json=payload)
    assert r.status_code == 422


def test_decision_list_rejects_bool_and_float_ids(client):
    # bool 不得被强转成整数 id（true 不能命中整数 1），float 同样拒绝
    payload = _mixed_payload(decisions=[{"id": True, "action": "abandon"}])
    assert client.post("/api/export", json=payload).status_code == 422
    payload = _mixed_payload(decisions=[{"id": 1.0, "action": "abandon"}])
    assert client.post("/api/export", json=payload).status_code == 422
    # choose 的 start/end 也不收 bool / float
    payload = _mixed_payload(decisions=[
        {"id": 1, "action": "choose", "start": True, "end": 24}])
    assert client.post("/api/export", json=payload).status_code == 422


# ---------- 入口校验：同类型重复 id 仍拒绝，区间非法仍 422 ----------

def test_duplicate_same_type_ids_still_rejected(client):
    payload = _mixed_payload(annotations=[
        {"id": 1, "start": 0, "end": 1},
        {"id": 1, "start": 1, "end": 2},
    ])
    r = client.post("/api/migrate", json=payload)
    assert r.status_code == 422
    assert "id 重复" in r.json()["detail"]

    payload = _mixed_payload(annotations=[
        {"id": "1", "start": 0, "end": 1},
        {"id": "1", "start": 1, "end": 2},
    ])
    r = client.post("/api/migrate", json=payload)
    assert r.status_code == 422
    assert "id 重复" in r.json()["detail"]


def test_mixed_int_and_string_same_value_are_distinct_inputs(client):
    # 1 与 "1" 同值不同类型：合法组合，入口不得拒收、不判重
    r = client.post("/api/migrate", json={
        "old_text": "ab",
        "new_text": "ab",
        "annotations": [
            {"id": 1, "start": 0, "end": 1},
            {"id": "1", "start": 1, "end": 2},
        ],
    })
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert [a["id"] for a in results] == [1, "1"]
    assert [a["status"] for a in results] == ["shifted", "shifted"]


# ---------- 顺序与原响应字段不变（非待确认批次直出） ----------

def test_export_response_shape_and_order_unchanged(client):
    r = client.post("/api/export", json={
        "old_text": "第一段：旧版说明。第二段：保持不变。",
        "new_text": "第一段：全新的新版说明。第二段：保持不变。",
        "annotations": [
            {"id": 9, "start": 13, "end": 17},
            {"id": "9", "start": 0, "end": 3},
        ],
    })
    assert r.status_code == 200
    anns = r.json()["annotations"]
    assert [a["id"] for a in anns] == [9, "9"]
    assert [type(a["id"]).__name__ for a in anns] == ["int", "str"]
    assert [(a["start"], a["end"], a["status"]) for a in anns] == [
        (16, 20, "shifted"), (0, 3, "shifted"),
    ]
