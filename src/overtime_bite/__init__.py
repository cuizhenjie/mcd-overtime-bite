"""OvertimeBite · 麦加班 —— 加班晚餐自动订餐助手。

基于麦当劳 MCP（McDonald's Capability Platform）能力平台。
"""

from .config import CENTS, OvertimeConfig, TasteProfile
from .models import (
    Candidate,
    Category,
    Channel,
    Coupon,
    MenuItem,
    Store,
    TriggerState,
)
from .pipeline import Decision, OvertimeBite
from .trigger import Action, TriggerDecision

__version__ = "0.1.0"

__all__ = [
    "CENTS",
    "Action",
    "Candidate",
    "Category",
    "Channel",
    "Coupon",
    "Decision",
    "MenuItem",
    "OvertimeBite",
    "OvertimeConfig",
    "Store",
    "TasteProfile",
    "TriggerDecision",
    "TriggerState",
]