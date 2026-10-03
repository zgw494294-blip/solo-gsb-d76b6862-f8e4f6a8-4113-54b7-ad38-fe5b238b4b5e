"""迁移核心逻辑测试（纯标准库，无需启动服务）。"""

import pytest

from app.migration import (
    ABANDONED,
    DISAMBIGUATED,
    MANUAL,
    MATCHED,
    PENDING,
    SHIFTED,
    DecisionError,
    apply_decisions,
    apply_decisions_typed,
    find_all,
    levenshtein,
    migrate_annotations,
)


def test_levenshtein_basic():
    assert levenshtein("", "") == 0
    assert levenshtein("abc", "abc") == 0
    assert levenshtein("abc", "") == 3
    assert levenshtein("kitten", "sitting") == 3
    assert levenshtein("第一章", "第二章") == 1


def test_find_all_overlapping():
    assert find_all("aaaa", "aa") == [0, 1, 2]
    assert find_all("abcabc", "abc") == [0, 3]
    assert find_all("abc", "x") == []


def test_shifted_inside_matching_block():
    old = "第一段：旧版说明。第二段：保持不变。"
    new = "第一段：全新的新版说明。第二段：保持不变。"
    # “保持不变”在旧版 [13, 17)，新版整体右移 3 个码点
    res = migrate_annotations(old, new, [{"id": "a1", "start": 13, "end": 17}])
    assert res[0]["status"] == SHIFTED
    assert (res[0]["new_start"], res[0]["new_end"]) == (16, 20)
    assert new[16:20] == "保持不变"


def test_matched_unique_hit():
    old = "口号：安全第一。标语：安全第一。"
    new = "口号：安全至上。标语：安全第一。"
    res = migrate_annotations(old, new, [{"id": "m", "start": 3, "end": 7}])  # 第一个“安全第一”
    assert res[0]["status"] == MATCHED
    assert new[res[0]["new_start"]:res[0]["new_end"]] == "安全第一"
    assert res[0]["new_start"] == new.find("安全第一")


def test_disambiguated_by_context():
    old = "甲章\n备注：暂无。\n乙章\n备注：暂无。\n丙章\n备注：暂无。\n"
    new = "甲章\n备注：已填。\n乙章\n备注：暂无。\n丙章\n备注：暂无。\n"
    res = migrate_annotations(old, new, [{"id": "d", "start": 3, "end": 9}])  # 甲章的“备注：暂无。”
    assert res[0]["status"] == DISAMBIGUATED
    assert new[res[0]["new_start"]:res[0]["new_end"]] == "备注：暂无。"
    # 两处命中（乙章、丙章），乙章上下文与旧版最接近
    assert res[0]["new_start"] == new.find("备注：暂无。")
    assert len(res[0]["candidates"]) == 2


def test_pending_on_tie():
    # 新版中两处命中，前后 20 码点上下文完全一致 → 并列待确认
    block = "起" * 20 + "目标文本" + "止" * 20
    old = block + "间隔" + block + "旧位：目标文本。"
    new = block + "间隔" + block + "旧位：已改写。"
    start = old.rfind("目标文本")
    res = migrate_annotations(old, new, [{"id": "t", "start": start, "end": start + 4}])
    assert res[0]["status"] == PENDING
    assert len(res[0]["candidates"]) == 2
    assert res[0]["candidates"][0]["distance"] == res[0]["candidates"][1]["distance"]


def test_pending_on_no_hit():
    old = "这段文字在新版中被整体删除。"
    new = "全新的内容。"
    res = migrate_annotations(old, new, [{"id": "n", "start": 0, "end": 6}])
    assert res[0]["status"] == PENDING
    assert res[0]["candidates"] == []


def test_code_point_offsets_with_astral_chars():
    # emoji 等为单码点；Python 切片天然按码点
    old = "🎉🎉目标🎉🎉"
    new = "🎉🎉🎉目标🎉🎉"
    res = migrate_annotations(old, new, [{"id": "e", "start": 2, "end": 4}])
    assert res[0]["status"] == SHIFTED
    assert (res[0]["new_start"], res[0]["new_end"]) == (3, 5)
    assert new[3:5] == "目标"


def _results_with_pending():
    old = "这段文字在新版中被整体删除。"
    new = "全新的内容。"
    return migrate_annotations(old, new, [{"id": "n", "start": 0, "end": 6}])


def test_apply_decisions_unresolved():
    exported, unresolved = apply_decisions(_results_with_pending(), {})
    assert exported == []
    assert unresolved == ["n"]


def test_apply_decisions_abandon():
    exported, unresolved = apply_decisions(_results_with_pending(), {"n": {"action": "abandon"}})
    assert unresolved == []
    assert exported == [{"id": "n", "start": None, "end": None, "status": ABANDONED}]


def test_apply_decisions_choose_and_invalid():
    block = "起" * 20 + "目标文本" + "止" * 20
    old = block + "间隔" + block + "旧位：目标文本。"
    new = block + "间隔" + block + "旧位：已改写。"
    start = old.rfind("目标文本")
    res = migrate_annotations(old, new, [{"id": "t", "start": start, "end": start + 4}])
    cand = res[0]["candidates"][0]

    exported, unresolved = apply_decisions(
        res, {"t": {"action": "choose", "start": cand["start"], "end": cand["end"]}}
    )
    assert unresolved == []
    assert exported == [{"id": "t", "start": cand["start"], "end": cand["end"], "status": MANUAL}]

    with pytest.raises(DecisionError):
        apply_decisions(res, {"t": {"action": "choose", "start": 0, "end": 4}})


# ---------- 整数 id 与字符串 id 不得混同 ----------

def _tie_results():
    """两处命中且上下文距离并列 → 待确认（带 2 个候选）。"""
    block = "起" * 20 + "目标文本" + "止" * 20
    old = block + "间隔" + block + "旧位：目标文本。"
    new = block + "间隔" + block + "旧位：已改写。"
    start = old.rfind("目标文本")
    res = migrate_annotations(old, new, [{"id": "t", "start": start, "end": start + 4}])
    assert res[0]["status"] == PENDING and len(res[0]["candidates"]) == 2
    return res


def _nohit_results(ann_id):
    """新版中无原文命中 → 待确认（无候选，只能废弃）。"""
    res = migrate_annotations("这段文字在新版中被整体删除。", "全新的内容。",
                              [{"id": ann_id, "start": 0, "end": 6}])
    assert res[0]["status"] == PENDING and res[0]["candidates"] == []
    return res


def _mixed_results():
    """同批两条待确认：整数 id 1（并列候选）与字符串 id "1"（无命中）。"""
    int_res = _tie_results()[0]
    int_res["id"] = 1
    str_res = _nohit_results("1")[0]
    return [int_res, str_res]


def test_mixed_ids_both_pending_without_decisions():
    results = _mixed_results()
    exported, unresolved = apply_decisions_typed(results, [])
    assert exported == []
    # 保留原 id 及其类型
    assert unresolved == [1, "1"]
    assert [type(x) for x in unresolved] == [int, str]


def test_legacy_dict_key_matching_multiple_ids_rejected():
    # 旧格式 decisions["1"] 同时命中整数 1 与字符串 "1"：不得猜测、不得一起废弃
    results = _mixed_results()
    with pytest.raises(DecisionError, match="多个不同类型"):
        apply_decisions(results, {"1": {"action": "abandon"}})
    # 未被偷偷处理：再用空决策仍两条待确认
    _, unresolved = apply_decisions_typed(results, [])
    assert unresolved == [1, "1"]


def test_legacy_dict_key_single_match_keeps_original_behavior():
    # 键只对应一个输入 id（纯字符串批次 / 纯整数批次）时保持原行为
    res = _nohit_results("n")
    exported, unresolved = apply_decisions(res, {"n": {"action": "abandon"}})
    assert unresolved == []
    assert exported == [{"id": "n", "start": None, "end": None, "status": ABANDONED}]

    res = _nohit_results(7)
    exported, unresolved = apply_decisions(res, {"7": {"action": "abandon"}})
    assert exported == [{"id": 7, "start": None, "end": None, "status": ABANDONED}]

    # 只存在整数 1 时键 "1" 唯一对应它，照常生效（不因类型不同而拒）
    res = _nohit_results(1)
    exported, unresolved = apply_decisions(res, {"1": {"action": "abandon"}})
    assert exported == [{"id": 1, "start": None, "end": None, "status": ABANDONED}]


def test_typed_decisions_locate_by_type_and_value():
    results = _mixed_results()

    # 只处理整数 1：字符串 "1" 仍待确认
    exported, unresolved = apply_decisions_typed(
        results, [{"id": 1, "action": "abandon"}])
    assert exported == [{"id": 1, "start": None, "end": None, "status": ABANDONED}]
    assert unresolved == ["1"]

    # 再单独处理字符串 "1"：整数 1 的废弃结果不受影响
    exported, unresolved = apply_decisions_typed(
        results, [{"id": 1, "action": "abandon"}, {"id": "1", "action": "abandon"}])
    assert unresolved == []
    assert exported == [
        {"id": 1, "start": None, "end": None, "status": ABANDONED},
        {"id": "1", "start": None, "end": None, "status": ABANDONED},
    ]
    assert [type(a["id"]) for a in exported] == [int, str]


def test_typed_choose_int_id_does_not_touch_string_id():
    results = _mixed_results()
    cand = results[0]["candidates"][0]
    exported, unresolved = apply_decisions_typed(
        results, [{"id": 1, "action": "choose",
                   "start": cand["start"], "end": cand["end"]}])
    assert exported == [{"id": 1, "start": cand["start"], "end": cand["end"],
                         "status": MANUAL}]
    assert unresolved == ["1"]  # 字符串 "1" 未被串用


def test_typed_decisions_unknown_id_rejected():
    res = _nohit_results("1")
    with pytest.raises(DecisionError, match="不存在"):
        apply_decisions_typed(res, [{"id": 1, "action": "abandon"}])
    with pytest.raises(DecisionError, match="不存在"):
        apply_decisions_typed(res, [{"id": "x", "action": "abandon"}])


def test_typed_decisions_duplicate_same_id_rejected():
    results = _mixed_results()
    with pytest.raises(DecisionError, match="多条决策"):
        apply_decisions_typed(results, [
            {"id": 1, "action": "abandon"},
            {"id": 1, "action": "abandon"},
        ])
    # 同类型重复（两个字符串键）也拒绝
    with pytest.raises(DecisionError, match="多条决策"):
        apply_decisions_typed(_nohit_results("a"), [
            {"id": "a", "action": "abandon"},
            {"id": "a", "action": "abandon"},
        ])


def test_typed_decisions_invalid_candidate_still_422():
    res = _tie_results()
    res[0]["id"] = 1
    # 整数 id 选择不在候选列表中的区间：同样抛 DecisionError
    with pytest.raises(DecisionError):
        apply_decisions_typed(res, [{"id": 1, "action": "choose",
                                     "start": 0, "end": 4}])
    # 动作非法
    with pytest.raises(DecisionError):
        apply_decisions_typed(res, [{"id": 1, "action": "what"}])


def test_typed_decisions_preserve_export_order_and_nonpending():
    # 非待确认项无需决策即按原序导出，且保留 id 类型
    res = migrate_annotations(
        "第一段：旧版说明。第二段：保持不变。",
        "第一段：全新的新版说明。第二段：保持不变。",
        [{"id": 1, "start": 13, "end": 17},
         {"id": "1", "start": 0, "end": 3}])
    exported, unresolved = apply_decisions_typed(res, [])
    assert unresolved == []
    assert [a["id"] for a in exported] == [1, "1"]
    assert [type(a["id"]) for a in exported] == [int, str]
