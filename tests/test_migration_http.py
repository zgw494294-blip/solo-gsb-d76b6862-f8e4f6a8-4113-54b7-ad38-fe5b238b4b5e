"""批注迁移 / 导出接口的 HTTP 回归（FastAPI TestClient）。

重点：同批同时含整数 id 1 与字符串 id "1"（二者都待确认）时，
- 人工决策必须以 id 的类型和值共同定位，一个 decisions["1"] 不得同时
  废弃两条；旧式 decisions 对象键只对应一个输入 id 时保持原行为，
  同时对应多个（1 与 "1"）时返回 422，不猜测；
- 新的 decision_items 列表逐项带原 id（保留 JSON 类型），只处理一条时
  另一条仍待确认，导出 409；分别处理后才可导出；
- /api/migrate、/api/export 的响应字段、顺序与迁移算法保持不变；
- 非法候选仍 422，同类型重复 id 仍拒绝，1 与 "1" 的合法组合不得拒收。
"""

import importlib
import json

import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # 临时 SQLite 路径，避免污染本地 data/copies.db
    monkeypatch.setenv("COPY_DB_PATH", str(tmp_path / "copies.db"))
    import app.main as main
    importlib.reload(main)
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c
    main._store = None


OLD = "甲：完全删除。乙：也删除。"
NEW = "全新的内容。"
# 两条区间不重叠、原文均不在新版出现 → 均 pending，id 分别为整数 1 / 字符串 "1"
ANNS_MIXED = [
    {"id": 1, "start": 0, "end": 8},
    {"id": "1", "start": 8, "end": 13},
]


def test_migrate_preserves_int_and_str_ids(client):
    r = client.post("/api/migrate", json={
        "old_text": OLD, "new_text": NEW, "annotations": ANNS_MIXED})
    assert r.status_code == 200
    results = r.json()["results"]
    assert [(x["id"], x["status"]) for x in results] == [
        (1, "pending"), ("1", "pending")]
    # 原始响应文本中数字 1 不带引号、字符串 "1" 带引号（类型未被强转）
    raw = r.content.decode("utf-8")
    assert '"id": 1' in raw.replace(" ", "") or '"id":1' in raw
    assert '"id":"1"' in raw.replace(" ", "")


def test_export_without_decisions_conflict_lists_both_types(client):
    r = client.post("/api/export", json={
        "old_text": OLD, "new_text": NEW, "annotations": ANNS_MIXED})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["message"] == "存在未处理的待确认批注，禁止导出"
    # pending_ids 保留原 id 及其类型与顺序
    assert detail["pending_ids"] == [1, "1"]
    assert [type(x).__name__ for x in detail["pending_ids"]] == ["int", "str"]


def test_legacy_decisions_object_applies_to_single_id_as_before(client):
    # 批次中只有字符串 "1"：旧对象键 "1" 唯一对应它，保持原行为
    anns = [{"id": "1", "start": 0, "end": 8}]
    r = client.post("/api/export", json={
        "old_text": OLD, "new_text": NEW, "annotations": anns,
        "decisions": {"1": {"action": "abandon"}}})
    assert r.status_code == 200
    assert r.json()["annotations"] == [
        {"id": "1", "start": None, "end": None, "status": "abandoned"}]

    # 批次中只有整数 1 时同样照常定位
    anns_int = [{"id": 1, "start": 0, "end": 8}]
    r = client.post("/api/export", json={
        "old_text": OLD, "new_text": NEW, "annotations": anns_int,
        "decisions": {"1": {"action": "abandon"}}})
    assert r.status_code == 200
    assert r.json()["annotations"] == [
        {"id": 1, "start": None, "end": None, "status": "abandoned"}]


def test_legacy_decisions_ambiguous_key_returns_422(client):
    # 同批含 1 与 "1" 且均待确认：decisions["1"] 无法定位，必须 422，
    # 不得把两条一起废弃
    r = client.post("/api/export", json={
        "old_text": OLD, "new_text": NEW, "annotations": ANNS_MIXED,
        "decisions": {"1": {"action": "abandon"}}})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "多个不同类型" in detail
    # 被拒绝时不产生任何导出副作用（接口本就无状态，这里确认仍是 422 而非 409/200）
    r2 = client.post("/api/export", json={
        "old_text": OLD, "new_text": NEW, "annotations": ANNS_MIXED})
    assert r2.status_code == 409
    assert r2.json()["detail"]["pending_ids"] == [1, "1"]


def test_decision_items_handle_one_then_other(client):
    # 只废弃整数 id 1：字符串 "1" 仍待确认 → 409
    r = client.post("/api/export", json={
        "old_text": OLD, "new_text": NEW, "annotations": ANNS_MIXED,
        "decision_items": [{"id": 1, "action": "abandon"}]})
    assert r.status_code == 409
    assert r.json()["detail"]["pending_ids"] == ["1"]

    # 反过来只处理字符串 "1"：整数 1 仍待确认
    r = client.post("/api/export", json={
        "old_text": OLD, "new_text": NEW, "annotations": ANNS_MIXED,
        "decision_items": [{"id": "1", "action": "abandon"}]})
    assert r.status_code == 409
    assert r.json()["detail"]["pending_ids"] == [1]

    # 分别处理两条后才可导出；输出顺序、原 id 与类型保持不变
    r = client.post("/api/export", json={
        "old_text": OLD, "new_text": NEW, "annotations": ANNS_MIXED,
        "decision_items": [
            {"id": 1, "action": "abandon"},
            {"id": "1", "action": "abandon"},
        ]})
    assert r.status_code == 200
    assert r.json()["annotations"] == [
        {"id": 1, "start": None, "end": None, "status": "abandoned"},
        {"id": "1", "start": None, "end": None, "status": "abandoned"},
    ]


def test_decision_items_serialized_types_are_distinct(client):
    # 直接检查请求体内两条 item 的 id JSON 类型不同且被分别对待
    body = {
        "old_text": OLD, "new_text": NEW, "annotations": ANNS_MIXED,
        "decision_items": [
            {"id": 1, "action": "abandon"},
            {"id": "1", "action": "abandon"},
        ],
    }
    compact = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    assert '{"id":1,"action":"abandon"}' in compact.replace(" ", "")
    assert '{"id":"1","action":"abandon"}' in compact.replace(" ", "")
    r = client.post("/api/export", json=body)
    assert r.status_code == 200


def test_decision_items_choose_does_not_cross_types(client):
    # 两条在新版中均有相同的两处并列候选；给整数 1 选一个候选，
    # 不得作用到字符串 "1"
    block = "起" * 20 + "目标文本" + "止" * 20
    old = block + "间隔" + block + "旧位：目标文本。"
    new = block + "间隔" + block + "旧位：已改写。"
    hit = old.rfind("目标文本")
    anns = [
        {"id": 1, "start": hit, "end": hit + 4},
        {"id": "1", "start": hit, "end": hit + 4},
    ]
    pre = client.post("/api/migrate", json={
        "old_text": old, "new_text": new, "annotations": anns}).json()["results"]
    assert [r["status"] for r in pre] == ["pending", "pending"]
    cand = pre[0]["candidates"][0]

    r = client.post("/api/export", json={
        "old_text": old, "new_text": new, "annotations": anns,
        "decision_items": [
            {"id": 1, "action": "choose",
             "start": cand["start"], "end": cand["end"]},
        ]})
    assert r.status_code == 409
    assert r.json()["detail"]["pending_ids"] == ["1"]

    r = client.post("/api/export", json={
        "old_text": old, "new_text": new, "annotations": anns,
        "decision_items": [
            {"id": 1, "action": "choose",
             "start": cand["start"], "end": cand["end"]},
            {"id": "1", "action": "abandon"},
        ]})
    assert r.status_code == 200
    assert r.json()["annotations"] == [
        {"id": 1, "start": cand["start"], "end": cand["end"], "status": "manual"},
        {"id": "1", "start": None, "end": None, "status": "abandoned"},
    ]


def test_invalid_candidate_returns_422(client):
    r = client.post("/api/export", json={
        "old_text": OLD, "new_text": NEW, "annotations": ANNS_MIXED,
        "decision_items": [
            {"id": 1, "action": "choose", "start": 0, "end": 4},
            {"id": "1", "action": "abandon"},
        ]})
    assert r.status_code == 422
    assert "候选" in r.json()["detail"]


def test_decision_items_unknown_and_non_pending_return_422(client):
    r = client.post("/api/export", json={
        "old_text": OLD, "new_text": NEW, "annotations": ANNS_MIXED,
        "decision_items": [{"id": 9, "action": "abandon"},
                           {"id": "9", "action": "abandon"}]})
    assert r.status_code == 422

    # 对非待确认（shifted）批注做决策 → 422
    old = new = "保持不变的内容"  # 7 个码点；新旧相同 → 全部 shifted
    r = client.post("/api/export", json={
        "old_text": old, "new_text": new,
        "annotations": [{"id": 1, "start": 0, "end": 3},
                        {"id": "1", "start": 4, "end": 7}],
        "decision_items": [{"id": 1, "action": "abandon"}]})
    assert r.status_code == 422
    assert "不是待确认" in r.json()["detail"]


def test_duplicate_same_type_id_still_rejected(client):
    r = client.post("/api/migrate", json={
        "old_text": OLD, "new_text": NEW,
        "annotations": [{"id": 1, "start": 0, "end": 1},
                        {"id": 1, "start": 1, "end": 2}]})
    assert r.status_code == 422
    assert "重复" in r.json()["detail"]

    r = client.post("/api/migrate", json={
        "old_text": OLD, "new_text": NEW,
        "annotations": [{"id": "1", "start": 0, "end": 1},
                        {"id": "1", "start": 1, "end": 2}]})
    assert r.status_code == 422


def test_duplicate_same_type_decision_item_rejected(client):
    r = client.post("/api/export", json={
        "old_text": OLD, "new_text": NEW, "annotations": ANNS_MIXED,
        "decision_items": [{"id": 1, "action": "abandon"},
                           {"id": 1, "action": "abandon"},
                           {"id": "1", "action": "abandon"}]})
    assert r.status_code == 422
    assert "决策重复" in r.json()["detail"]


def test_two_decision_forms_together_rejected(client):
    r = client.post("/api/export", json={
        "old_text": OLD, "new_text": NEW, "annotations": ANNS_MIXED,
        "decisions": {"1": {"action": "abandon"}},
        "decision_items": [{"id": 1, "action": "abandon"}]})
    assert r.status_code == 422


def test_responses_unchanged_for_normal_input(client):
    # 迁移算法与导出响应（字段、顺序）对普通输入保持原样
    old = "第一段：旧版说明。第二段：保持不变。"
    new = "第一段：全新的新版说明。第二段：保持不变。"
    anns = [{"id": "a1", "start": 13, "end": 17}]
    r = client.post("/api/migrate", json={
        "old_text": old, "new_text": new, "annotations": anns})
    res = r.json()["results"][0]
    assert res["status"] == "shifted"
    assert (res["new_start"], res["new_end"]) == (16, 20)

    r = client.post("/api/export", json={
        "old_text": old, "new_text": new, "annotations": anns})
    assert r.status_code == 200
    assert r.json() == {"annotations": [
        {"id": "a1", "start": 16, "end": 20, "status": "shifted"}]}
