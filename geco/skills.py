"""OSRS experience table helpers."""
import math

MAX_LEVEL = 126  # virtual levels past 99 are allowed for goals


def _build():
    table = [0, 0]  # index = level
    points = 0
    for lvl in range(1, MAX_LEVEL):
        points += math.floor(lvl + 300 * 2 ** (lvl / 7.0))
        table.append(points // 4)
    return table


XP_TABLE = _build()  # XP_TABLE[level] = xp needed for that level


def xp_for_level(level):
    level = max(1, min(MAX_LEVEL, int(level)))
    return XP_TABLE[level]


def level_for_xp(xp, cap=99):
    lvl = 1
    for L in range(2, cap + 1):
        if xp >= XP_TABLE[L]:
            lvl = L
        else:
            break
    return lvl
