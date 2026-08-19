"""Read access to the known set.

``known_set`` is a view, not a table: it merges the Anki mirror's maturity rule
(``ivl >= 21`` days) with manual marks, latest mark winning, and exposes
``suspect`` as a separate flag rather than folding it into ``is_known`` — a
suspicion is a reason to look again, not a verdict. Nothing here writes; marks
are made through :func:`katagiri.events.mark_item` so that every change to the
known set also lands in the event log.

Lookups accept either an item id or a surface form. Ids are resolved through the
alias table first, so a renamed item keeps answering to its old id and the caller
is told a redirect happened. A surface form is matched against ``item.kanji`` and
``item.reading``; an ambiguous surface returns its candidates instead of picking
one, because guessing which 明日 the learner meant is exactly the kind of quiet
wrong answer this project cannot afford.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from katagiri.db import resolve_alias

_KNOWN_COLUMNS = "item_id, is_known, source, suspect, manual_mark"


def _known_row(conn: sqlite3.Connection, item_id: str) -> sqlite3.Row | None:
    return conn.execute(
        f"SELECT {_KNOWN_COLUMNS} FROM known_set WHERE item_id = ?", (item_id,)
    ).fetchone()


def _surface_matches(conn: sqlite3.Connection, surface: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, kind, kanji, reading
          FROM item
         WHERE kanji = ? OR reading = ?
         ORDER BY id
        """,
        (surface, surface),
    ).fetchall()


def known_word(conn: sqlite3.Connection, item_id_or_surface: str) -> dict[str, Any]:
    """Is this item or surface form in the known set?

    Returns ``{"query", "item_id", "found", "is_known", "source", "suspect",
    "manual_mark", "redirected", "matched_by"}``. ``found`` is False when neither
    the id nor the surface resolves to anything the known set has heard of —
    distinct from ``is_known=False``, which is a real answer.

    ``matched_by`` is ``"item_id"``, ``"alias"``, ``"surface"``, or ``None``. When
    a surface matches several items the result carries ``ambiguous=True`` and a
    ``candidates`` list, and no verdict.
    """
    query = item_id_or_surface.strip()
    if not query:
        raise ValueError("known_word needs a non-empty item id or surface form.")

    resolved = resolve_alias(conn, query)
    canonical = resolved["canonical_id"]

    row = _known_row(conn, canonical)
    if row is not None:
        return _verdict(
            query=query,
            row=row,
            redirected=resolved["redirected"],
            matched_by="alias" if resolved["redirected"] else "item_id",
        )

    matches = _surface_matches(conn, query)
    if len(matches) > 1:
        return {
            "query": query,
            "item_id": None,
            "found": True,
            "ambiguous": True,
            "candidates": [
                {
                    "item_id": match["id"],
                    "kind": match["kind"],
                    "kanji": match["kanji"],
                    "reading": match["reading"],
                }
                for match in matches
            ],
            "is_known": None,
            "source": None,
            "suspect": None,
            "manual_mark": None,
            "redirected": False,
            "matched_by": "surface",
        }
    if len(matches) == 1:
        surface_row = _known_row(conn, matches[0]["id"])
        if surface_row is not None:
            return _verdict(
                query=query,
                row=surface_row,
                redirected=False,
                matched_by="surface",
            )

    return {
        "query": query,
        "item_id": canonical if resolved["redirected"] else None,
        "found": False,
        "ambiguous": False,
        "is_known": None,
        "source": None,
        "suspect": None,
        "manual_mark": None,
        "redirected": resolved["redirected"],
        "matched_by": None,
    }


def _verdict(
    *, query: str, row: sqlite3.Row, redirected: bool, matched_by: str
) -> dict[str, Any]:
    return {
        "query": query,
        "item_id": row["item_id"],
        "found": True,
        "ambiguous": False,
        "is_known": bool(row["is_known"]),
        "source": row["source"],
        "suspect": bool(row["suspect"]),
        "manual_mark": row["manual_mark"],
        "redirected": redirected,
        "matched_by": matched_by,
    }


def known_set_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Shape of the known set: totals, and the split by item kind and source.

    ``known_set`` carries no ``kind`` column — it is keyed by id and includes ids
    that only exist as marks — so kind comes from a LEFT JOIN onto ``item`` and
    rows with no item row are counted under ``"unlinked"``. Those are marks on
    ids that have not been imported yet, and losing sight of them is precisely
    what the view's UNION exists to prevent.
    """
    totals = conn.execute(
        """
        SELECT COUNT(*)                       AS total,
               SUM(is_known)                  AS known,
               SUM(CASE WHEN is_known = 0 THEN 1 ELSE 0 END) AS unknown,
               SUM(suspect)                   AS suspect
          FROM known_set
        """
    ).fetchone()

    by_source = {
        row["source"]: {"total": row["total"], "known": row["known"]}
        for row in conn.execute(
            """
            SELECT source, COUNT(*) AS total, SUM(is_known) AS known
              FROM known_set
             GROUP BY source
             ORDER BY source
            """
        )
    }

    by_kind = {
        (row["kind"] or "unlinked"): {"total": row["total"], "known": row["known"]}
        for row in conn.execute(
            """
            SELECT i.kind AS kind, COUNT(*) AS total, SUM(k.is_known) AS known
              FROM known_set k
              LEFT JOIN item i ON i.id = k.item_id
             GROUP BY i.kind
             ORDER BY i.kind
            """
        )
    }

    # The latest mark per item, matching the rule the view itself applies —
    # counting every historical mark row would double-count an item that was
    # marked unknown and later known.
    marks = {
        row["mark"]: row["items"]
        for row in conn.execute(
            """
            SELECT m.mark AS mark, COUNT(DISTINCT m.item_id) AS items
              FROM manual_marks m
             WHERE m.ts = (SELECT MAX(m2.ts) FROM manual_marks m2
                            WHERE m2.item_id = m.item_id)
             GROUP BY m.mark
             ORDER BY m.mark
            """
        )
    }

    return {
        "total": int(totals["total"] or 0),
        "known": int(totals["known"] or 0),
        "unknown": int(totals["unknown"] or 0),
        "suspect": int(totals["suspect"] or 0),
        "by_source": by_source,
        "by_kind": by_kind,
        "latest_marks_by_value": marks,
    }


__all__ = ["known_set_stats", "known_word"]
