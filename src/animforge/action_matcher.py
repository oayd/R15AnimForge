"""Smart ASBR / CC2 animation action formula learner and best-effort role matcher."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Sequence

_COMBAT_OR_BAD_TOKENS = {
    "atk", "dmg", "damage", "hit", "dead", "die", "down", "throw", "grb",
    "hha", "gha", "sp", "win", "lose", "ko", "guard", "gd", "brk", "break",
    "end", "ed", "land", "bnd", "bound", "wall", "ukemi",
}
_DIRECTION_TOKENS = {"front", "back", "left", "right", "side", "up", "dw", "down"}
_CORE_LOCOMOTION_TOKENS = {"nut", "jump", "walk", "dash", "step", "std", "idle", "wait"}
_LOOP_TOKENS = {"lp", "loop"}


@dataclass(frozen=True)
class ActionMatchResult:
    source: str | None
    kind: str  # "exact" | "formula" | "alias" | "missing"
    reason: str


def tokenize_action(name: str, charcode: str = "") -> list[str]:
    """Normalize an action name into ordered lowercase tokens, stripping charcode prefix."""
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    tokens = s.split()
    if not tokens:
        return []

    cc = (charcode or "").strip().lower()
    if cc and cc not in ("auto", "*"):
        if tokens[0] == cc or re.fullmatch(rf"{re.escape(cc)}(?:t\d+)?", tokens[0]):
            tokens = tokens[1:]
        elif tokens[0].startswith(cc) and len(tokens[0]) <= len(cc) + 3:
            tokens = tokens[1:]
        elif re.fullmatch(r"\d[a-z0-9]{3,}(?:t\d+)?", tokens[0]):
            tokens = tokens[1:]
    else:
        if re.fullmatch(r"\d[a-z0-9]{3,}(?:t\d+)?", tokens[0]):
            tokens = tokens[1:]

    return tokens


def _is_subsequence(needle: Sequence[str], haystack: Sequence[str]) -> bool:
    it = iter(haystack)
    return all(tok in it for tok in needle)


def learn_stance_tag(available_actions: Sequence[str], charcode: str = "") -> str | None:
    """Detect if a character's core locomotion uses an inserted stance tag like `bas_r_*`."""
    tag_counts: Counter[str] = Counter()
    plain_count = 0
    for act in available_actions:
        toks = tokenize_action(act, charcode)
        if len(toks) >= 2 and toks[0] == "bas":
            if toks[1] in _CORE_LOCOMOTION_TOKENS:
                plain_count += 1
            elif len(toks) >= 3 and len(toks[1]) <= 3 and toks[2] in _CORE_LOCOMOTION_TOKENS:
                tag_counts[toks[1]] += 1

    if not tag_counts:
        return None
    best_tag, best_cnt = tag_counts.most_common(1)[0]
    if plain_count == 0 or best_cnt >= plain_count:
        return best_tag
    return None


def _score_candidate(
    cand_tokens: list[str],
    target_tokens: list[str],
    *,
    learned_tag: str | None,
    allow_combat: bool = False,
) -> float | None:
    if not _is_subsequence(target_tokens, cand_tokens):
        return None

    target_set = set(target_tokens)
    score = 0.0
    for idx, tok in enumerate(cand_tokens):
        if tok in target_set:
            continue
        if not allow_combat and tok in _COMBAT_OR_BAD_TOKENS:
            return None
        if tok in _COMBAT_OR_BAD_TOKENS:
            score += 200.0
        elif tok in _DIRECTION_TOKENS:
            score += 50.0
        elif tok in _LOOP_TOKENS:
            score += 30.0
        elif tok == "st":
            score += 22.0
        elif idx == 1 and cand_tokens[0] == "bas" and len(tok) <= 3:
            score += 0.1 if tok == learned_tag else 1.5
        elif re.fullmatch(r"\d{1,3}", tok):
            score += 0.5
        else:
            score += 12.0

    score += 0.01 * len(cand_tokens)
    return score


def _best_formula_match(
    pattern_lists: Sequence[list[str]],
    available_actions: Sequence[str],
    *,
    charcode: str,
    learned_tag: str | None,
    allow_combat: bool = False,
) -> tuple[str | None, float]:
    best_name: str | None = None
    best_score = float("inf")
    for p_idx, p_toks in enumerate(pattern_lists):
        if not p_toks:
            continue
        base_bias = p_idx * 5.0
        for act in available_actions:
            c_toks = tokenize_action(act, charcode)
            sc = _score_candidate(c_toks, p_toks, learned_tag=learned_tag, allow_combat=allow_combat)
            if sc is None:
                continue
            total = base_bias + sc
            if total < best_score:
                best_score = total
                best_name = act
    return best_name, best_score


def resolve_action_for_role(
    target_name: str,
    role: str,
    available_actions: Sequence[str],
    *,
    charcode: str = "",
    learned_tag: str | None = None,
) -> ActionMatchResult:
    """Resolve an expected profile action name against discovered clips using exact, formula, and role-alias matching."""
    avail = list(available_actions)
    if not avail:
        return ActionMatchResult(None, "missing", "No actions discovered")

    # 1. Exact & case-insensitive exact
    if target_name in avail:
        return ActionMatchResult(target_name, "exact", "Exact match")
    low_map = {a.lower(): a for a in avail}
    if target_name.lower() in low_map:
        return ActionMatchResult(low_map[target_name.lower()], "exact", "Case-insensitive exact match")

    target_toks = tokenize_action(target_name, charcode)
    if learned_tag is None:
        learned_tag = learn_stance_tag(avail, charcode)

    # Exact normalized token match (handles archive suffix like _001 or separator differences)
    for act in avail:
        c_toks = tokenize_action(act, charcode)
        c_base = c_toks[:-1] if (len(c_toks) > len(target_toks) and re.fullmatch(r"\d{1,3}", c_toks[-1])) else c_toks
        if c_base == target_toks:
            return ActionMatchResult(act, "exact", "Normalized exact match")

    # 2. Formula subsequence match (handles `7val01_bas_r_jump_lp` for `bas_jump_lp`, etc.)
    hit, sc = _best_formula_match([target_toks], avail, charcode=charcode, learned_tag=learned_tag)
    if hit is not None and sc < 10.0:
        return ActionMatchResult(hit, "formula", f"Learned formula match -> {hit}")

    # 3. Role-specific alias & best-effort fallback ladder
    role_patterns: dict[str, list[list[str]]] = {
        "base_action": [
            ["bas", "nut", "st"],
            ["bas", "nut"],
            ["bas", "idle", "st"],
            ["bas", "idle"],
            ["bas", "wait"],
            ["bas", "stand"],
            ["nut", "st"],
            ["nut"],
            ["idle"],
            ["wait"],
            ["stand"],
        ],
        "breathing_action": [
            ["bas", "nut"],
            ["bas", "nut", "st"],
            ["bas", "idle"],
            ["bas", "wait"],
            ["nut"],
            ["idle"],
            ["wait"],
        ],
        "crouch_action": [
            ["bas", "nut", "jump"],
            ["bas", "jump", "st"],
            ["bas", "jump", "start"],
            ["bas", "jump"],
            ["jump"],
            ["bas", "nut"],
            ["nut"],
        ],
        "launch_action": [
            ["bas", "jump"],
            ["jump"],
            ["bas", "fly"],
            ["fall"],
        ],
        "hang_action": [
            ["bas", "jump", "lp"],
            ["jump", "lp"],
            ["bas", "fall", "lp"],
            ["fall", "lp"],
            ["bas", "fall"],
            ["fall"],
            ["bas", "jump", "loop"],
            ["jump", "loop"],
            ["bas", "jump"],
            ["jump"],
        ],
        "Walk": [
            ["bas", "walk", "front"],
            ["bas", "walk"],
            ["walk", "front"],
            ["walk"],
        ],
        "Run": [
            ["bas", "dash", "front"],
            ["bas", "run", "front"],
            ["bas", "dash"],
            ["bas", "run"],
            ["dash"],
            ["run"],
        ],
    }

    candidates = role_patterns.get(role)
    if candidates is None:
        # Derive patterns from target name if role is custom
        if "jump" in target_toks and "lp" in target_toks:
            candidates = role_patterns["hang_action"]
        elif "nut" in target_toks and "jump" in target_toks:
            candidates = role_patterns["crouch_action"]
        elif "jump" in target_toks:
            candidates = role_patterns["launch_action"]
        elif "nut" in target_toks and "st" in target_toks:
            candidates = role_patterns["base_action"]
        elif "nut" in target_toks:
            candidates = role_patterns["breathing_action"]
        else:
            candidates = [target_toks]

    hit_alias, _ = _best_formula_match(candidates, avail, charcode=charcode, learned_tag=learned_tag)
    if hit_alias is not None:
        return ActionMatchResult(hit_alias, "alias", f"Best match -> {hit_alias}")

    # Directional fallback as last resort for jump/fall/walk/run
    if hit is not None:
        return ActionMatchResult(hit, "alias", f"Fallback match -> {hit}")

    return ActionMatchResult(None, "missing", f"Missing: {target_name}")


def resolve_builder_fields(
    cfg: dict[str, object],
    available_actions: Sequence[str],
    *,
    charcode: str = "",
) -> dict[str, ActionMatchResult]:
    """Resolve all source action fields of a procedural builder recipe."""
    learned = learn_stance_tag(available_actions, charcode)
    results: dict[str, ActionMatchResult] = {}
    for field in ("base_action", "breathing_action", "crouch_action", "launch_action", "hang_action"):
        val = cfg.get(field)
        if val:
            results[field] = resolve_action_for_role(
                str(val),
                field,
                available_actions,
                charcode=charcode,
                learned_tag=learned,
            )
    return results
