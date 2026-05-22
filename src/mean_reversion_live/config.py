"""Environment-backed settings."""
from __future__ import annotations
import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Polymarket endpoints
    gamma_base_url: str = "https://gamma-api.polymarket.com"
    clob_base_url: str = "https://clob.polymarket.com"
    clob_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    data_api_base_url: str = "https://data-api.polymarket.com"

    coinbase_base_url: str = "https://api.coinbase.com"

    # Symbol set
    symbols: str = "btc,eth,sol,xrp"

    # Where the canonical signals.py / simulate.py / config.py / portfolio.py live
    polymarket_arb_path: str = "/Users/itayozer/dev/polymarket-arb"

    # Logging
    log_level: str = "INFO"
    log_format: str = "console"  # console | json

    # Repo-relative paths (resolved against REPO_ROOT)
    data_dir: str = "data"
    logs_dir: str = "logs"
    strategies_yaml: str = "strategies.yaml"

    @property
    def symbol_list(self) -> list:
        return [s.strip().lower() for s in self.symbols.split(",") if s.strip()]

    @property
    def data_path(self) -> Path:
        return REPO_ROOT / self.data_dir

    @property
    def logs_path(self) -> Path:
        return REPO_ROOT / self.logs_dir

    @property
    def strategies_path(self) -> Path:
        return REPO_ROOT / self.strategies_yaml

    @property
    def kill_sentinel(self) -> Path:
        return self.data_path / "KILL"

    @property
    def state_path(self) -> Path:
        return self.data_path / "state"

    @property
    def portfolios_path(self) -> Path:
        return self.data_path / "portfolios"

    @property
    def jsonl_path(self) -> Path:
        return self.data_path / "jsonl"

    @property
    def live_data_path(self) -> Path:
        return self.data_path / "live"

    @property
    def historical_data_path(self) -> Path:
        return self.data_path / "historical"

    @property
    def outcomes_path(self) -> Path:
        return self.data_path / "outcomes.csv"


_settings: Settings = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        # Always cd into REPO_ROOT so relative paths resolve consistently.
        if Path(os.getcwd()) != REPO_ROOT:
            os.chdir(REPO_ROOT)
        _settings = Settings()
    return _settings
