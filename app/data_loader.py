import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import yaml  # pip install pyyaml

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

class _FileCache:
    def __init__(self) -> None:
        self._cache: Dict[str, Tuple[float, Any]] = {}

    def load_yaml(self, path: str) -> Any:
        abspath = os.path.join(DATA_DIR, path)
        mtime = os.path.getmtime(abspath)
        cached = self._cache.get(abspath)
        if cached and cached[0] == mtime:
            return cached[1]
        with open(abspath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        self._cache[abspath] = (mtime, data)
        return data

_cache = _FileCache()

def get_leagues() -> List[Dict[str, Any]]:
    data = _cache.load_yaml("leagues.yaml")
    return data.get("leagues", [])

def get_teams(league_code: Optional[str] = None) -> List[Dict[str, Any]]:
    data = _cache.load_yaml("teams.yaml")
    teams = data.get("teams", [])
    if league_code:
        teams = [t for t in teams if t.get("league") == league_code]
    return teams

def get_sources(team_code: Optional[str] = None, include_disabled: bool = False) -> List[Dict[str, Any]]:
    data = _cache.load_yaml("sources.yaml")
    sources = data.get("sources", [])
    if not include_disabled:
        sources = [s for s in sources if s.get("enabled", True)]
    if team_code:
        sources = [s for s in sources if team_code in (s.get("teams") or [])]
    return sources
