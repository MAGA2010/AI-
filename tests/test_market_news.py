# -*- coding: utf-8 -*-
"""Tests for market_news.py — automated market-wide news fetcher."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from alphasift.market_news import (
    collect_market_news,
    fetch_market_activity,
    fetch_market_headlines,
    fetch_policy_news,
    _cache_path,
    _read_cache,
    _write_cache,
)


# ── Cache tests ───────────────────────────────────────────────

def test_write_and_read_cache(tmp_path):
    _write_cache(tmp_path, "test news content")
    result = _read_cache(tmp_path, ttl_hours=4)
    assert result == "test news content"


def test_cache_expired(tmp_path):
    _write_cache(tmp_path, "old news")
    # Manually backdate the timestamp
    cache_file = _cache_path(tmp_path)
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    data["timestamp"] = "2020-01-01T00:00:00"
    cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    result = _read_cache(tmp_path, ttl_hours=4)
    assert result is None


def test_cache_missing_dir(tmp_path):
    result = _read_cache(tmp_path / "nonexistent", ttl_hours=4)
    assert result is None


def test_cache_corrupted_json(tmp_path):
    cache_file = _cache_path(tmp_path)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text("not json", encoding="utf-8")
    result = _read_cache(tmp_path, ttl_hours=4)
    assert result is None


# ── collect_market_news integration ───────────────────────────

def test_collect_market_news_returns_string():
    """Should return a string (may be empty if network unavailable)."""
    result = collect_market_news(
        providers=["headlines"],
        max_chars=200,
        cache_dir=None,
        headlines_limit=5,
    )
    assert isinstance(result, str)


def test_collect_market_news_empty_providers():
    result = collect_market_news(providers=[], max_chars=200)
    assert result == ""


def test_collect_market_news_respects_max_chars(tmp_path):
    """Write a long cache entry, verify truncation."""
    long_text = "x" * 2000
    _write_cache(tmp_path, long_text)
    result = collect_market_news(
        providers=["headlines"],
        max_chars=300,
        cache_dir=tmp_path,
    )
    assert len(result) <= 300


def test_collect_market_news_uses_cache(tmp_path):
    _write_cache(tmp_path, "cached market news")
    # Should return cached content without fetching
    result = collect_market_news(
        providers=["headlines"],
        max_chars=200,
        cache_dir=tmp_path,
    )
    assert "cached market news" in result


# ── Individual fetcher tests (network-dependent, may be empty) ──

def test_fetch_market_headlines_returns_string():
    result = fetch_market_headlines(limit=3)
    assert isinstance(result, str)


def test_fetch_policy_news_returns_string():
    result = fetch_policy_news(limit=3)
    assert isinstance(result, str)


def test_fetch_market_activity_returns_string():
    result = fetch_market_activity()
    assert isinstance(result, str)
