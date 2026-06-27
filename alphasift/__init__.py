# -*- coding: utf-8 -*-
"""alphasift — 自动选股 Skill"""

__version__ = "0.3.0"

from alphasift.pipeline import screen
from alphasift.evaluate import evaluate_saved_run, evaluate_saved_runs
from alphasift.strategy import list_strategies
from alphasift.audit import audit_project

__all__ = [
    "__version__",
    "screen",
    "evaluate_saved_run",
    "evaluate_saved_runs",
    "list_strategies",
    "audit_project",
    "WebResearcher",
    "DataSourceManager",
    "SmartResearcher",
]

# 新模块 - 延迟导入，避免未安装时崩溃
try:
    from alphasift.web_research import WebResearcher
except ImportError:
    pass

try:
    from alphasift.data_manager import DataSourceManager
except ImportError:
    pass

try:
    from alphasift.smart_researcher import SmartResearcher
except ImportError:
    pass
