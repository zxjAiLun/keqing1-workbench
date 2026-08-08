"""Local rating foundation for battle results.

This is intentionally small and file-backed: it gives the battle API a stable
profile/rating boundary before multiplayer accounts are introduced.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from mahjong_env.final_rank import final_ranks


DEFAULT_RATING = 1500.0
K_FACTOR = 24.0


@dataclass
class BattleProfile:
    player_id: str
    display_name: str
    rating: float = DEFAULT_RATING
    games: int = 0
    first_places: int = 0
    average_rank_sum: float = 0.0
    updated_at: float = field(default_factory=time.time)

    @property
    def average_rank(self) -> float:
        return self.average_rank_sum / self.games if self.games else 0.0

    def to_public_dict(self) -> dict:
        data = asdict(self)
        data["average_rank"] = round(self.average_rank, 3)
        data["rating"] = round(self.rating, 1)
        return data


class RatingStore:
    def __init__(self, path: Path | None = None):
        project_root = Path(__file__).resolve().parents[2]
        self.path = path or (project_root / "artifacts" / "battle" / "ratings.json")

    def _load(self) -> dict[str, BattleProfile]:
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        profiles: dict[str, BattleProfile] = {}
        for player_id, item in raw.get("profiles", {}).items():
            if not isinstance(item, dict):
                continue
            profiles[player_id] = BattleProfile(
                player_id=str(item.get("player_id") or player_id),
                display_name=str(item.get("display_name") or player_id),
                rating=float(item.get("rating", DEFAULT_RATING)),
                games=int(item.get("games", 0)),
                first_places=int(item.get("first_places", 0)),
                average_rank_sum=float(item.get("average_rank_sum", 0.0)),
                updated_at=float(item.get("updated_at", time.time())),
            )
        return profiles

    def _save(self, profiles: dict[str, BattleProfile]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "profiles": {
                player_id: asdict(profile)
                for player_id, profile in sorted(profiles.items())
            },
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_profiles(self) -> list[dict]:
        return [profile.to_public_dict() for profile in self._load().values()]

    def get_profile(self, player_id: str, display_name: str | None = None) -> dict:
        profiles = self._load()
        profile = profiles.get(player_id)
        if profile is None:
            profile = BattleProfile(player_id=player_id, display_name=display_name or player_id)
            profiles[player_id] = profile
            self._save(profiles)
        elif display_name and profile.display_name != display_name:
            profile.display_name = display_name
            profile.updated_at = time.time()
            self._save(profiles)
        return profile.to_public_dict()

    def record_result(
        self,
        *,
        players: Sequence[tuple[str, str]],
        scores: Sequence[int | float],
        initial_oya: int = 0,
    ) -> list[dict]:
        if len(players) != 4 or len(scores) != 4:
            raise ValueError("rating results require four players and four scores")
        profiles = self._load()
        ranks = final_ranks(scores, initial_oya=initial_oya)
        current = []
        for player_id, display_name in players:
            profile = profiles.get(player_id)
            if profile is None:
                profile = BattleProfile(player_id=player_id, display_name=display_name)
                profiles[player_id] = profile
            current.append(profile)

        avg_rating = sum(profile.rating for profile in current) / 4.0
        updated: list[dict] = []
        for idx, profile in enumerate(current):
            rank_index = ranks[idx]
            rank = rank_index + 1
            actual_score = (3 - rank_index) / 3.0
            expected_score = 1.0 / (1.0 + math.pow(10.0, (avg_rating - profile.rating) / 400.0))
            delta = K_FACTOR * (actual_score - expected_score)
            profile.rating += delta
            profile.games += 1
            profile.first_places += 1 if rank == 1 else 0
            profile.average_rank_sum += rank
            profile.updated_at = time.time()
            item = profile.to_public_dict()
            item["rank"] = rank
            item["score"] = scores[idx]
            item["rating_delta"] = round(delta, 1)
            updated.append(item)

        self._save(profiles)
        return updated


def battle_player_identity(raw_id: object, name: object, seat: int) -> tuple[str, str]:
    display_name = str(name or f"P{seat}")
    player_id = str(raw_id or display_name or f"seat-{seat}")
    return player_id, display_name
