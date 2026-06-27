# -*- coding: utf-8 -*-
"""Tests for filter.py — sub-new exclusion and consecutive loss filters."""

from datetime import datetime, timedelta

import pandas as pd
import pytest

from alphasift.filter import apply_filters, filter_single_snapshot
from alphasift.models import HardFilterConfig


# ── Sub-new exclusion tests ───────────────────────────────────

def test_filter_sub_new_excluded():
    cfg = HardFilterConfig(exclude_st=False, exclude_sub_new=True, sub_new_min_days=60)
    recent = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    snap = {"name": "次新股份", "code": "301001", "list_date": recent}
    assert filter_single_snapshot(snap, cfg) is False


def test_filter_sub_new_passes_if_old_enough():
    cfg = HardFilterConfig(exclude_st=False, exclude_sub_new=True, sub_new_min_days=60)
    old = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d")
    snap = {"name": "老股股份", "code": "600001", "list_date": old}
    assert filter_single_snapshot(snap, cfg) is True


def test_filter_sub_new_no_list_date_field():
    """When list_date is absent, filter should be a no-op (keep stock)."""
    cfg = HardFilterConfig(exclude_st=False, exclude_sub_new=True, sub_new_min_days=60)
    snap = {"name": "未知上市日期", "code": "600002"}
    assert filter_single_snapshot(snap, cfg) is True


def test_filter_sub_new_disabled():
    cfg = HardFilterConfig(exclude_st=False, exclude_sub_new=False)
    recent = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    snap = {"name": "次新股份", "code": "301001", "list_date": recent}
    assert filter_single_snapshot(snap, cfg) is True


# ── Consecutive loss years filter tests ───────────────────────

def test_filter_consecutive_loss_excluded():
    cfg = HardFilterConfig(exclude_st=False, exclude_consecutive_loss_years=3)
    snap = {"name": "亏损股份", "code": "600003", "consecutive_loss_years": 3}
    assert filter_single_snapshot(snap, cfg) is False


def test_filter_consecutive_loss_passes():
    cfg = HardFilterConfig(exclude_st=False, exclude_consecutive_loss_years=3)
    snap = {"name": "盈利股份", "code": "600004", "consecutive_loss_years": 1}
    assert filter_single_snapshot(snap, cfg) is True


def test_filter_consecutive_loss_no_field():
    """When consecutive_loss_years is absent, filter should be a no-op."""
    cfg = HardFilterConfig(exclude_st=False, exclude_consecutive_loss_years=3)
    snap = {"name": "无数据股份", "code": "600005"}
    assert filter_single_snapshot(snap, cfg) is True


def test_filter_consecutive_loss_disabled():
    cfg = HardFilterConfig(exclude_st=False, exclude_consecutive_loss_years=None)
    snap = {"name": "亏损股份", "code": "600003", "consecutive_loss_years": 5}
    assert filter_single_snapshot(snap, cfg) is True


# ── DataFrame-level tests ─────────────────────────────────────

def test_apply_filters_sub_new_dataframe():
    cfg = HardFilterConfig(exclude_st=False, exclude_sub_new=True, sub_new_min_days=60)
    recent = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    old = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d")
    df = pd.DataFrame({
        "name": ["次新", "老股"],
        "code": ["301001", "600001"],
        "list_date": [recent, old],
        "price": [20.0, 20.0],
    })
    result = apply_filters(df, cfg)
    assert len(result) == 1
    assert result.iloc[0]["code"] == "600001"


def test_apply_filters_consecutive_loss_dataframe():
    cfg = HardFilterConfig(exclude_st=False, exclude_consecutive_loss_years=3)
    df = pd.DataFrame({
        name: ["盈利", "亏损"]
        for name in ["name", "code", "price", "consecutive_loss_years"]
    } if False else {
        "name": ["盈利", "亏损"],
        "code": ["600001", "600002"],
        "price": [20.0, 20.0],
        "consecutive_loss_years": [1, 4],
    })
    result = apply_filters(df, cfg)
    assert len(result) == 1
    assert result.iloc[0]["name"] == "盈利"
