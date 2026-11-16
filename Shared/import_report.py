"""Per-import problem list (main thread only).

Import steps call ``warn`` / ``fail`` for non-fatal losses the user would
otherwise never see (missing texture, failed material, failed mod attach,
failed extraction). The operator calls ``begin`` before and ``end`` after,
then reports the count. Calls outside a report are dropped at the next
outer ``begin``.
"""

from __future__ import annotations

MAX_ITEMS = 200

_kind = ""
_items: list[str] = []
_dropped = 0
_depth = 0


def begin(kind: str) -> None:
    """Start a report. Nested begins share the outer report."""
    global _kind, _items, _dropped, _depth
    if _depth == 0:
        _kind = str(kind or "Import")
        _items = []
        _dropped = 0
    _depth += 1


def _add(text: str) -> None:
    global _dropped
    if len(_items) >= MAX_ITEMS:
        _dropped += 1
        return
    _items.append(text)


def warn(msg: str) -> None:
    _add(str(msg))


def fail(what: str, exc: BaseException | None = None) -> None:
    if exc is None:
        _add(str(what))
    else:
        _add(f"{what}: {type(exc).__name__}: {exc}")


def count() -> int:
    return len(_items) + _dropped


def end() -> dict:
    """Close the report. Returns {kind, count, items, first, text}.

    Only the outermost ``end`` returns the list; inner ones return an empty
    summary so nested imports do not report twice.
    """
    global _depth, _items, _dropped
    if _depth > 1:
        _depth -= 1
        return {"kind": _kind, "count": 0, "items": [], "first": "", "text": ""}
    _depth = 0
    items = list(_items)
    total = len(items) + _dropped
    _items = []
    _dropped = 0
    text = ""
    if total:
        text = f"{total} problem(s): {items[0] if items else ''}"
        if total > 1:
            text += f" (+{total - 1} more, see console)"
    return {
        "kind": _kind,
        "count": total,
        "items": items,
        "first": items[0] if items else "",
        "text": text,
    }


def abort() -> None:
    """Drop any open report (cancelled or failed run)."""
    global _depth, _items, _dropped
    _depth = 0
    _items = []
    _dropped = 0


def emit(operator, summary: dict) -> None:
    """Print the full list and raise one WARNING on ``operator`` when n > 0."""
    n = int(summary.get("count") or 0)
    if n <= 0:
        return
    kind = summary.get("kind") or "Import"
    print(f"Arc Asset Importer: {kind} finished with {n} problem(s):")
    for line in summary.get("items") or ():
        print(f"  - {line}")
    hidden = n - len(summary.get("items") or ())
    if hidden > 0:
        print(f"  ... {hidden} more not kept")
    if operator is not None:
        try:
            operator.report({'WARNING'}, f"{kind}: {summary.get('text')}")
        except Exception:
            pass
