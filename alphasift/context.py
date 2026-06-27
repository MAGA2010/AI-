# -*- coding: utf-8 -*-
"""Context assembly for LLM ranking."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

_CANDIDATE_CONTEXT_COLUMNS = {
    "news": "新闻",
    "announcement": "公告",
    "announcements": "公告",
    "fund_flow": "资金流",
    "fundflow": "资金流",
    "summary": "摘要",
    "context": "上下文",
    "context_summary": "压缩摘要",
    "text": "文本",
    "risk": "风险",
    "catalyst": "催化",
    "source_count": "来源数",
    "source_confidence": "来源置信度",
    "source_weight_score": "来源权重分",
    "event_tags": "事件标签",
    "announcement_categories": "公告类别",
    "negative_event_flags": "负面风险",
}


def build_llm_context(
    *,
    base_context: str = "",
    context_files: list[str | Path] | None = None,
    candidate_context_files: list[str | Path] | None = None,
    candidate_context_rows: list[dict[str, object]] | None = None,
    market_news_text: str = "",
    snapshot_df: pd.DataFrame | None = None,
    candidate_df: pd.DataFrame | None = None,
    event_profile: dict[str, object] | None = None,
    max_chars: int = 4000,
) -> str:
    """Build bounded context text for the LLM soft ranker."""
    sections: list[str] = []
    if base_context.strip():
        sections.append("【人工上下文】\n" + base_context.strip())

    if market_news_text.strip():
        sections.append("【市场资讯】\n" + market_news_text.strip())

    file_context = _read_context_files(context_files or [])
    if file_context:
        sections.append("【上下文文件】\n" + file_context)

    event_profile_context = summarize_event_profile(event_profile)
    if event_profile_context:
        sections.append(event_profile_context)

    candidate_external_context = _read_candidate_context_files(
        candidate_context_files or [],
        candidate_df,
    )
    if candidate_external_context:
        sections.append("【候选外部线索】\n" + candidate_external_context)

    collected_candidate_context = _format_candidate_context_rows(
        candidate_context_rows or [],
        candidate_df,
    )
    if collected_candidate_context:
        sections.append("【候选抓取线索】\n" + collected_candidate_context)

    snapshot_context = summarize_snapshot_context(snapshot_df, title="全市场快照")
    if snapshot_context:
        sections.append(snapshot_context)

    candidate_context = summarize_snapshot_context(candidate_df, title="候选池快照")
    if candidate_context:
        sections.append(candidate_context)

    candidate_profile = summarize_candidate_profile(candidate_df)
    if candidate_profile:
        sections.append(candidate_profile)

    combined = "\n\n".join(sections).strip()
    if not combined:
        return ""
    if len(combined) <= max_chars:
        return combined
    return combined[: max_chars - 20].rstrip() + "\n...[truncated]"


def summarize_snapshot_context(df: pd.DataFrame | None, *, title: str) -> str:
    """Summarize breadth, activity and extremes from a snapshot DataFrame."""
    if df is None or df.empty:
        return ""

    lines = [f"【{title}】", f"样本数: {len(df)}"]
    if "change_pct" in df.columns:
        change = pd.to_numeric(df["change_pct"], errors="coerce").dropna()
        if not change.empty:
            positive_ratio = (change > 0).mean() * 100
            lines.append(
                "涨跌分布: "
                f"上涨占比 {positive_ratio:.1f}%, "
                f"中位涨跌幅 {change.median():.2f}%, "
                f"平均涨跌幅 {change.mean():.2f}%"
            )
            lines.append("涨跌极值: " + _format_extremes(df, change, ascending=False))
    if "amount" in df.columns:
        amount = pd.to_numeric(df["amount"], errors="coerce").dropna()
        if not amount.empty:
            lines.append(f"成交额中位数: {amount.median():.0f}")
    if "volume_ratio" in df.columns:
        volume_ratio = pd.to_numeric(df["volume_ratio"], errors="coerce").dropna()
        if not volume_ratio.empty:
            hot_ratio = (volume_ratio >= 2).mean() * 100
            lines.append(f"量比>=2占比: {hot_ratio:.1f}%")
    return "\n".join(lines)


def summarize_candidate_profile(df: pd.DataFrame | None) -> str:
    """Summarize factor conflicts and leadership inside the candidate pool."""
    if df is None or df.empty:
        return ""

    lines = ["【候选池结构】"]
    factor_cols = {
        "价值": "factor_value_score",
        "流动性": "factor_liquidity_score",
        "动量": "factor_momentum_score",
        "反转": "factor_reversal_score",
        "活跃度": "factor_activity_score",
        "稳定性": "factor_stability_score",
        "市值容量": "factor_size_score",
        "主题热度": "factor_theme_heat_score",
    }
    available = {label: col for label, col in factor_cols.items() if col in df.columns}
    if available:
        averages = []
        for label, col in available.items():
            series = pd.to_numeric(df[col], errors="coerce").dropna()
            if not series.empty:
                averages.append(f"{label}{series.mean():.1f}")
        if averages:
            lines.append("因子均值: " + "，".join(averages))

        leaders = []
        columns = [col for col in ("code", "name") if col in df.columns]
        for label, col in available.items():
            series = pd.to_numeric(df[col], errors="coerce")
            if series.dropna().empty or not columns:
                continue
            idx = series.idxmax()
            row = df.loc[idx]
            leaders.append(f"{label}:{row.get('code', '')}{row.get('name', '')}({series.loc[idx]:.1f})")
        if leaders:
            lines.append("因子领先: " + "，".join(leaders[:8]))

    if "screen_score" in df.columns:
        screen_score = pd.to_numeric(df["screen_score"], errors="coerce").dropna()
        if not screen_score.empty:
            lines.append(
                f"主评分分布: 最高{screen_score.max():.1f}，"
                f"中位{screen_score.median():.1f}，最低{screen_score.min():.1f}"
            )

    industry_summary = _summarize_label_distribution(df, "industry")
    if industry_summary:
        lines.append("行业分布: " + industry_summary)
    concept_summary = _summarize_label_distribution(df, "concepts")
    if concept_summary:
        lines.append("概念线索: " + concept_summary)
    heat_summary = _summarize_board_heat(df)
    if heat_summary:
        lines.append("板块/主题热度: " + heat_summary)

    return "\n".join(lines) if len(lines) > 1 else ""


def summarize_event_profile(event_profile: dict[str, object] | None) -> str:
    """Summarize strategy-level event preferences for the LLM."""
    if not event_profile:
        return ""
    lines = ["【策略事件偏好】"]
    field_labels = {
        "preferred_event_tags": "偏好事件标签",
        "avoided_event_tags": "规避事件标签",
        "preferred_announcement_categories": "偏好公告类别",
        "avoided_announcement_categories": "规避公告类别",
        "notes": "事件备注",
    }
    for field, label in field_labels.items():
        value = _format_profile_value(event_profile.get(field))
        if value:
            lines.append(f"{label}: {value}")
    source_weights = event_profile.get("source_weights")
    if isinstance(source_weights, dict) and source_weights:
        items = []
        for source, weight in source_weights.items():
            text = _safe_context_value(source, max_len=40)
            if not text:
                continue
            try:
                items.append(f"{text}={float(weight):.2f}")
            except (TypeError, ValueError):
                continue
        if items:
            lines.append("来源权重: " + "，".join(items))
    return "\n".join(lines) if len(lines) > 1 else ""


def _read_context_files(paths: list[str | Path]) -> str:
    chunks: list[str] = []
    for path_like in paths:
        path = Path(path_like)
        if not path.is_file():
            raise FileNotFoundError(f"Context file not found: {path}")
        text = path.read_text(encoding="utf-8").strip()
        if text:
            chunks.append(f"# {path.name}\n{text}")
    return "\n\n".join(chunks)


def _read_candidate_context_files(
    paths: list[str | Path],
    candidate_df: pd.DataFrame | None,
) -> str:
    if not paths or candidate_df is None or candidate_df.empty or "code" not in candidate_df.columns:
        return ""

    candidate_names = {
        _normalize_code(row.get("code", "")): str(row.get("name", "") or "")
        for _, row in candidate_df.iterrows()
    }
    candidate_names = {code: name for code, name in candidate_names.items() if code}
    candidate_codes = set(candidate_names)
    chunks: list[str] = []
    for path_like in paths:
        path = Path(path_like)
        if not path.is_file():
            raise FileNotFoundError(f"Candidate context file not found: {path}")
        rows = _load_candidate_context_rows(path)
        chunks.extend(_format_candidate_context_row(row, candidate_codes, candidate_names) for row in rows)
    return "\n".join(item for item in chunks if item)


def _format_candidate_context_rows(
    rows: list[dict[str, object]],
    candidate_df: pd.DataFrame | None,
) -> str:
    if not rows or candidate_df is None or candidate_df.empty or "code" not in candidate_df.columns:
        return ""
    candidate_names = {
        _normalize_code(row.get("code", "")): str(row.get("name", "") or "")
        for _, row in candidate_df.iterrows()
    }
    candidate_names = {code: name for code, name in candidate_names.items() if code}
    candidate_codes = set(candidate_names)
    chunks = [
        _format_candidate_context_row(row, candidate_codes, candidate_names)
        for row in rows
    ]
    return "\n".join(item for item in chunks if item)


def _format_candidate_context_row(
    row: dict[str, object],
    candidate_codes: set[str],
    candidate_names: dict[str, str],
) -> str:
    code = _normalize_code(row.get("code", row.get("代码", "")))
    if code not in candidate_codes:
        return ""
    fields = []
    for column, label in _CANDIDATE_CONTEXT_COLUMNS.items():
        value = _safe_context_value(row.get(column))
        if value:
            fields.append(f"{label}:{value}")
    if not fields:
        return ""
    name = _safe_context_value(row.get("name") or row.get("名称")) or candidate_names.get(code, "")
    return f"- {code} {name}: " + "；".join(fields)


def _load_candidate_context_rows(path: Path) -> list[dict[str, object]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path, dtype=str).fillna("").to_dict(orient="records")
    if suffix == ".jsonl":
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                item = json.loads(line)
                if isinstance(item, dict):
                    rows.append(item)
        return rows
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            items = data.get("items") or data.get("data")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
            rows = []
            for code, value in data.items():
                if isinstance(value, dict):
                    rows.append({"code": code, **value})
                elif isinstance(value, str):
                    rows.append({"code": code, "text": value})
            return rows
    raise ValueError(f"Unsupported candidate context file format: {path}")


def _safe_context_value(value: object, *, max_len: int = 280) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        text = ",".join(str(item).strip() for item in value if str(item).strip())
    else:
        text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "<na>"}:
        return ""
    return text[:max_len]


def _format_profile_value(value: object) -> str:
    if isinstance(value, list):
        return "，".join(
            item
            for item in (_safe_context_value(raw, max_len=80) for raw in value)
            if item
        )
    return _safe_context_value(value, max_len=280)


def _normalize_code(value: object) -> str:
    text = _safe_context_value(value, max_len=80)
    if not text:
        return ""
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if text.isdigit():
        return text.zfill(6)[-6:]
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
    if match:
        return match.group(1)
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.zfill(6)[-6:] if digits else ""


def _format_extremes(df: pd.DataFrame, change: pd.Series, *, ascending: bool) -> str:
    columns = [col for col in ("code", "name", "change_pct") if col in df.columns]
    if not columns:
        return ""
    top = df.loc[change.sort_values(ascending=ascending).head(3).index, columns]
    items = []
    for _, row in top.iterrows():
        code = str(row.get("code", ""))
        name = str(row.get("name", ""))
        pct = row.get("change_pct", 0)
        items.append(f"{code}{name}({float(pct):.2f}%)")
    return ", ".join(items)


def _summarize_label_distribution(df: pd.DataFrame, column: str) -> str:
    if column not in df.columns:
        return ""
    labels: list[str] = []
    for raw in df[column].dropna().astype(str):
        for item in raw.replace("，", ",").replace("、", ",").split(","):
            label = item.strip()
            if label and label.lower() not in {"nan", "none", "<na>"}:
                labels.append(label)
    if not labels:
        return ""
    counts = pd.Series(labels).value_counts().head(6)
    return "，".join(f"{label}{count}" for label, count in counts.items())


def _summarize_board_heat(df: pd.DataFrame, *, limit: int = 5) -> str:
    if "board_heat_score" not in df.columns:
        return ""
    values = pd.to_numeric(df["board_heat_score"], errors="coerce")
    if values.dropna().empty:
        return ""
    items = []
    for idx in values.sort_values(ascending=False).dropna().head(limit).index:
        row = df.loc[idx]
        code = str(row.get("code", "") or "")
        name = str(row.get("name", "") or "")
        label = str(row.get("board_heat_summary", "") or row.get("industry", "") or "")
        trend = _safe_context_value(row.get("board_heat_trend_score"), max_len=20)
        persistence = _safe_context_value(row.get("board_heat_persistence_score"), max_len=20)
        cooling = _safe_context_value(row.get("board_heat_cooling_score"), max_len=20)
        state = _safe_context_value(row.get("board_heat_state"), max_len=20)
        trend_text = f",trend={trend}" if trend else ""
        persistence_text = f",persist={persistence}" if persistence else ""
        cooling_text = f",cooling={cooling}" if cooling else ""
        state_text = f",state={state}" if state else ""
        items.append(
            f"{code}{name}:{float(values.loc[idx]):.1f}"
            f"{trend_text}{persistence_text}{cooling_text}{state_text}({label[:60]})"
        )
    return "，".join(items)
