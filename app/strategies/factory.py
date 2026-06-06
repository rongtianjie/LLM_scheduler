from typing import Optional

from fastapi import Request

from app.strategies.base import PriorityStrategy
from app.strategies.api_key_based import ApiKeyPriorityStrategy


def create_strategy(strategy_name: str) -> PriorityStrategy:
    if strategy_name == "api_key":
        return ApiKeyPriorityStrategy()
    else:
        raise ValueError(f"Unknown priority strategy: {strategy_name}")
