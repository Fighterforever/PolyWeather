from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _winning_token(row: Dict[str, Any]) -> str:
    for field in ("winning_token_id", "winningTokenId", "winning_token"):
        value = _text(row.get(field))
        if value:
            return value
    token_map = row.get("token_id_by_outcome") if isinstance(row.get("token_id_by_outcome"), dict) else {}
    yes_payout = _safe_float(row.get("settled_yes_payout"))
    if yes_payout == 1.0:
        return _text(token_map.get("Yes") or token_map.get("yes"))
    if yes_payout == 0.0:
        return _text(token_map.get("No") or token_map.get("no"))
    tokens = row.get("tokens") if isinstance(row.get("tokens"), list) else []
    for token in tokens:
        if not isinstance(token, dict):
            continue
        if bool(token.get("winner")) or _safe_float(token.get("payout")) == 1.0:
            return _text(token.get("token_id") or token.get("id"))
    return ""


def build_eq_dead_no_resolved_audits(
    *,
    fills: Iterable[Dict[str, Any]],
    closed_markets: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    closed_by_slug = {
        _text(row.get("market_slug") or row.get("slug")): row
        for row in closed_markets
        if isinstance(row, dict) and _text(row.get("market_slug") or row.get("slug"))
    }
    rows: List[Dict[str, Any]] = []
    for fill in fills:
        if not isinstance(fill, dict):
            continue
        market = closed_by_slug.get(_text(fill.get("market_slug")))
        entry_price = _safe_float(fill.get("entry_price"))
        winning_token = _winning_token(market or {})
        resolved = market is not None and bool(winning_token)
        payout = None
        pnl = None
        if resolved and entry_price is not None:
            payout = 1.0 if winning_token == _text(fill.get("token_id")) else 0.0
            pnl = round((payout - float(entry_price)) * 100.0, 6)
        rows.append(
            {
                "schema_version": "polyweather_eq_dead_no_resolved_audit.v1",
                "fill_id": fill.get("fill_id"),
                "strategy_id": "eq_dead_no_lock",
                "market_slug": fill.get("market_slug"),
                "token_id": fill.get("token_id"),
                "entry_price": entry_price,
                "resolved": bool(resolved),
                "winning_token_id": winning_token,
                "payout": payout,
                "resolved_pnl_cents": pnl,
                "missing_resolution_reason": None if resolved else "missing_closed_market_or_winning_token",
                "paper_only": True,
                "counts_for_live_gate": False,
                "live_order_path": False,
            }
        )
    return rows


def build_eq_dead_no_resolved_audit_report(
    *,
    fills: Iterable[Dict[str, Any]],
    closed_markets: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    fill_rows = [row for row in fills if isinstance(row, dict)]
    audits = build_eq_dead_no_resolved_audits(fills=fill_rows, closed_markets=closed_markets)
    resolved = [row for row in audits if row.get("resolved_pnl_cents") is not None]
    return {
        "schema_version": "polyweather_eq_dead_no_resolved_audit_report.v1",
        "paper_only": True,
        "counts_for_live_gate": False,
        "live_order_path": False,
        "fill_count": len(fill_rows),
        "audit_count": len(audits),
        "resolved_fill_count": len(resolved),
        "unresolved_fill_count": len(audits) - len(resolved),
        "resolved_pnl_cents": round(sum(float(row["resolved_pnl_cents"]) for row in resolved), 6) if resolved else None,
        "rows": audits,
    }
