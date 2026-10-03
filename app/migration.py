"""批注迁移核心逻辑。

偏移约定：所有 start/end 均为 Unicode 码点偏移，区间为左闭右开 [start, end)。
Python 字符串按码点索引，因此切片结果天然符合该约定。
"""

from __future__ import annotations

import difflib

CONTEXT_LEN = 20  # 命中位置前后各取的码点数

# 状态取值
SHIFTED = "shifted"              # 完整落在同一匹配块，直接平移
MATCHED = "matched"              # 新版中唯一命中批注原文
DISAMBIGUATED = "disambiguated"  # 多处命中，上下文距离最小者胜出
MANUAL = "manual"                # 人工选择候选
ABANDONED = "abandoned"          # 人工标记废弃
PENDING = "pending"              # 待确认（命中并列或无命中）


class DecisionError(ValueError):
    """人工决策不合法（如所选区间不在候选列表中）。"""


def id_key(ann_id) -> tuple[str, object]:
    """以 id 的类型与值共同定位批注。

    JSON 里整数 1 与字符串 "1" 是两个不同的合法 id，键里必须保留类型，
    否则一个 decisions["1"] 会同时命中两条批注。
    """
    return type(ann_id).__name__, ann_id


def typed_decisions(items: list[dict]) -> dict[tuple[str, object], dict]:
    """把显式带类型的决策列表整理为 {(类型名, id): 决策}。

    每个决策形如 {"id": <原 id，保留 JSON 类型>, "action": ..., ...}；
    同一 (类型, 值) 出现两次即重复决策，抛 DecisionError。
    """
    by_key: dict[tuple[str, object], dict] = {}
    for d in items:
        key = id_key(d["id"])
        if key in by_key:
            _, value = key
            raise DecisionError(f"批注 id 的决策重复：{value!r}")
        by_key[key] = d
    return by_key


def legacy_decisions(
    results: list[dict], decisions: dict[str, dict]
) -> dict[tuple[str, object], dict]:
    """把旧式 {"id字符串": 决策} 映射到带类型的键。

    旧对象键只能给出字符串形式，无法区分整数 1 与字符串 "1"：该键在本批
    输入里只对应一个 id 时按原值定位、保持原行为；同时对应整数与字符串
    两个 id 时无法确定目标，拒绝整批（不得猜测）。需要分别处理时改用
    decision_items 列表形式。未知键维持历史行为（忽略）。
    """
    groups: dict[str, list[tuple[str, object]]] = {}
    for r in results:
        groups.setdefault(str(r["id"]), []).append(id_key(r["id"]))
    by_key: dict[tuple[str, object], dict] = {}
    for raw_key, d in decisions.items():
        keys = groups.get(raw_key, [])
        if len(keys) > 1:
            kinds = " / ".join(
                f"{tid}:{value!r}" for tid, value in keys)
            raise DecisionError(
                f"决策键 {raw_key!r} 同时匹配多个不同类型的批注 id（{kinds}），"
                "旧式 decisions 对象无法区分；请改用 decision_items 列表，"
                '在每项中直接给出原 id（如 {"id": 1} 与 {"id": "1"}）')
        if keys:
            by_key[keys[0]] = d
    return by_key


def levenshtein(a: str, b: str) -> int:
    """按码点计算两个字符串的 Levenshtein 编辑距离。"""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def find_all(haystack: str, needle: str) -> list[int]:
    """返回 needle 在 haystack 中所有（含重叠）出现的码点起点。"""
    starts = []
    pos = haystack.find(needle)
    while pos != -1:
        starts.append(pos)
        pos = haystack.find(needle, pos + 1)
    return starts


def _contexts(text: str, start: int, end: int) -> tuple[str, str]:
    """取区间 [start, end) 前后各 CONTEXT_LEN 个码点作为上下文。"""
    return text[max(0, start - CONTEXT_LEN):start], text[end:end + CONTEXT_LEN]


def migrate_annotations(old_text: str, new_text: str, annotations: list[dict]) -> list[dict]:
    """逐条迁移批注，返回与输入同序的结果列表。"""
    matcher = difflib.SequenceMatcher(None, old_text, new_text, autojunk=False)
    blocks = [b for b in matcher.get_matching_blocks() if b.size > 0]
    return [_migrate_one(old_text, new_text, ann, blocks) for ann in annotations]


def _migrate_one(old_text: str, new_text: str, ann: dict, blocks: list) -> dict:
    start, end = ann["start"], ann["end"]
    length = end - start
    excerpt = old_text[start:end]
    base = {"id": ann["id"], "start": start, "end": end, "text": excerpt}

    # 1) 批注完整落在同一匹配块内 → 直接平移
    for blk in blocks:
        if blk.a <= start and end <= blk.a + blk.size:
            new_start = blk.b + (start - blk.a)
            return {**base, "status": SHIFTED,
                    "new_start": new_start, "new_end": new_start + length,
                    "candidates": [], "detail": "完整落在匹配块内，直接平移"}

    # 2) 在新版中搜索批注原文
    hits = find_all(new_text, excerpt)
    old_before, old_after = _contexts(old_text, start, end)

    def make_candidate(hit: int) -> dict:
        new_before, new_after = _contexts(new_text, hit, hit + length)
        dist = levenshtein(old_before, new_before) + levenshtein(old_after, new_after)
        return {"start": hit, "end": hit + length, "distance": dist}

    if len(hits) == 1:
        c = make_candidate(hits[0])
        return {**base, "status": MATCHED,
                "new_start": c["start"], "new_end": c["end"],
                "candidates": [c], "detail": "新版中唯一命中批注原文"}

    if len(hits) > 1:
        candidates = [make_candidate(h) for h in hits]
        best = min(c["distance"] for c in candidates)
        winners = [c for c in candidates if c["distance"] == best]
        if len(winners) == 1:
            w = winners[0]
            return {**base, "status": DISAMBIGUATED,
                    "new_start": w["start"], "new_end": w["end"],
                    "candidates": candidates,
                    "detail": f"{len(hits)} 处命中，上下文距离 {best} 最小者胜出"}
        return {**base, "status": PENDING, "new_start": None, "new_end": None,
                "candidates": candidates,
                "detail": f"{len(hits)} 处命中且最小上下文距离 {best} 并列，待确认"}

    return {**base, "status": PENDING, "new_start": None, "new_end": None,
            "candidates": [], "detail": "新版中未找到批注原文，待确认"}


def apply_decisions_typed(
    results: list[dict], by_key: dict[tuple[str, object], dict],
    strict_targets: bool = True,
) -> tuple[list[dict], list]:
    """把已按 (类型, 值) 定位的人工决策应用到迁移结果上。

    返回 (exported, unresolved)：exported 为可导出条目，顺序与输入一致；
    unresolved 为仍为待确认且未给出决策的批注 id（保留原 id 及其类型，
    非空时调用方应拒绝导出）。决策动作未知或候选区间不合法均抛
    DecisionError；strict_targets 为真时（新列表形式），引用不存在的 id
    或对非待确认批注做决策同样拒绝；旧式对象形式保持历史行为（忽略）。
    """
    result_by_key = {id_key(r["id"]): r for r in results}
    if strict_targets:
        for key in by_key:
            if key not in result_by_key:
                _, value = key
                raise DecisionError(f"决策引用的批注 id 不存在：{value!r}")
            if result_by_key[key]["status"] != PENDING:
                _, value = key
                raise DecisionError(f"批注 {value} 不是待确认项，无需人工决策")

    exported, unresolved = [], []
    for r in results:
        if r["status"] != PENDING:
            exported.append({"id": r["id"], "start": r["new_start"],
                             "end": r["new_end"], "status": r["status"]})
            continue
        d = by_key.get(id_key(r["id"]))
        if d is None:
            unresolved.append(r["id"])
        elif d.get("action") == "abandon":
            exported.append({"id": r["id"], "start": None, "end": None, "status": ABANDONED})
        elif d.get("action") == "choose":
            hit = (d.get("start"), d.get("end"))
            if not any((c["start"], c["end"]) == hit for c in r["candidates"]):
                raise DecisionError(f"批注 {r['id']} 选择的区间 {hit} 不在候选列表中")
            exported.append({"id": r["id"], "start": d["start"], "end": d["end"], "status": MANUAL})
        else:
            raise DecisionError(f"批注 {r['id']} 的决策动作未知：{d.get('action')}")
    return exported, unresolved


def apply_decisions(results: list[dict], decisions: dict[str, dict]) -> tuple[list[dict], list]:
    """把旧式对象决策（键为 id 字符串）应用到迁移结果上。

    返回 (exported, unresolved)：exported 为可导出条目；
    unresolved 为仍为待确认且未给出决策的批注 id（非空时调用方应拒绝导出）。
    键同时对应整数与字符串两个不同 id 时抛 DecisionError，不得猜测；
    需要区分 id 类型时用 typed_decisions + apply_decisions_typed。
    旧式对象的其余历史行为不变：未知键、对非待确认项的决策被忽略。
    """
    return apply_decisions_typed(
        results, legacy_decisions(results, decisions), strict_targets=False)
