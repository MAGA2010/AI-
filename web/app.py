# -*- coding: utf-8 -*-
"""AlphaSift Web — FastAPI backend for the stock screening web interface."""

from __future__ import annotations

import io
import sys
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

# Ensure alphasift package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alphasift.strategy import list_strategies
from alphasift.market_news import collect_market_news

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

app = FastAPI(title="AlphaSift Web", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STRATEGIES_DIR = Path(__file__).resolve().parent.parent / "strategies"

# Ensure working directory and env vars are set correctly
import os
_ALPHASIFT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_ALPHASIFT_ROOT)
os.environ["ALPHASIFT_DATA_DIR"] = str(_ALPHASIFT_ROOT / "data")
os.environ["STRATEGIES_DIR"] = str(_ALPHASIFT_ROOT / "strategies")


# ═══════════════════════════════════════════════════════════════
#  Schemas
# ═══════════════════════════════════════════════════════════════

class ScreenRequest(BaseModel):
    strategy: str = "full_spectrum_v2"
    use_llm: bool = True
    fast_mode: bool = False
    max_output: int = 10
    context: str = ""


# ═══════════════════════════════════════════════════════════════
#  API Endpoints
# ═══════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the web frontend."""
    html_path = Path(__file__).resolve().parent / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/api/strategies")
async def get_strategies():
    """List all available strategies."""
    strategies = list_strategies(STRATEGIES_DIR)
    return {
        "strategies": [
            {
                "name": s.name,
                "display_name": s.display_name,
                "description": s.description,
                "category": s.category,
                "tags": s.tags,
                "version": s.version,
            }
            for s in strategies
        ]
    }


@app.post("/api/screen")
async def run_screen(req: ScreenRequest):
    """Run stock screening with the given strategy."""
    try:
        from alphasift.pipeline import screen
        from alphasift.config import Config

        config = Config.from_env()

        # Speed optimizations: skip per-stock news, limit LLM candidates
        config.llm_candidate_context_enabled = False  # skip per-stock news (biggest time sink)
        config.llm_max_candidates = 10                 # fewer LLM calls
        config.llm_timeout_sec = 20.0                  # shorter timeout
        config.llm_max_retries = 0                     # no retries on timeout
        config.daily_enrich_enabled = False             # skip daily K-line (slow)
        config.market_news_enabled = False              # skip market news (cached separately)

        # Fast mode = skip LLM entirely, pure quantitative scoring (~5s)
        use_llm = req.use_llm and not req.fast_mode

        result = screen(
            req.strategy,
            use_llm=use_llm,
            max_output=req.max_output,
            llm_context=req.context if req.context else None,
            collect_llm_candidate_context=False,
            daily_enrich=False,
            config=config,
        )

        picks = []
        for p in result.picks:
            picks.append({
                "rank": p.rank,
                "code": p.code,
                "name": p.name,
                "price": p.price,
                "change_pct": p.change_pct,
                "screen_score": round(p.screen_score, 1),
                "final_score": round(p.final_score, 1),
                "llm_score": p.llm_score,
                "llm_confidence": p.llm_confidence,
                "llm_thesis": p.llm_thesis,
                "llm_sector": p.llm_sector,
                "llm_catalysts": p.llm_catalysts,
                "llm_risks": p.llm_risks,
                "buy_signal": p.buy_signal,
                "sell_signals": p.sell_signals,
                "suggested_position_pct": p.suggested_position_pct,
                "stop_loss_price": p.stop_loss_price,
                "stop_profit_price": p.stop_profit_price,
                "risk_level": p.risk_level,
                "risk_flags": p.risk_flags,
                "industry": p.industry,
                "concepts": p.concepts,
                "pe_ratio": p.pe_ratio,
                "pb_ratio": p.pb_ratio,
                "turnover_rate": p.turnover_rate,
                "volume_ratio": p.volume_ratio,
                "total_mv": p.total_mv,
                "change_60d": p.change_60d,
                "signal_score": p.signal_score,
                "ma_bullish": p.ma_bullish,
                "macd_status": p.macd_status,
                "track_policy_score": p.track_policy_score,
                "moat_score": p.moat_score,
                "financial_health_score": p.financial_health_score,
                "buy_condition_fundamental": p.buy_condition_fundamental,
                "buy_condition_technical": p.buy_condition_technical,
                "buy_condition_valuation": p.buy_condition_valuation,
            })

        return {
            "success": True,
            "strategy": result.strategy,
            "snapshot_count": result.snapshot_count,
            "after_filter_count": result.after_filter_count,
            "pick_count": len(result.picks),
            "llm_ranked": result.llm_ranked,
            "llm_market_view": result.llm_market_view,
            "llm_selection_logic": result.llm_selection_logic,
            "degradation": result.degradation,
            "picks": picks,
        }
    except Exception as exc:
        logger.exception("Screening failed")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/market-news")
async def get_market_news():
    """Fetch current market-wide news."""
    try:
        text = collect_market_news(max_chars=2000)
        return {"success": True, "text": text}
    except Exception as exc:
        return {"success": False, "text": "", "error": str(exc)}


# ═══════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
