"""P717 Team Mode — collaborative roles architecture."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from engines.platform_sdk.types import TeamRole

ROOT = Path(__file__).resolve().parents[2]


class TeamService:
    """
    Architecture prep for multi-user projects.
    Roles: translator, editor, dub director, sound engineer.
    """

    def __init__(self, store: Path | str | None = None) -> None:
        self.store = Path(store or (ROOT / "data" / "platform_team.json"))
        self._data: dict[str, Any] = {"projects": {}}
        if self.store.is_file():
            try:
                self._data = json.loads(self.store.read_text(encoding="utf-8"))
            except Exception:
                pass

    def _save(self) -> None:
        self.store.parent.mkdir(parents=True, exist_ok=True)
        self.store.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")

    def assign_role(self, project_id: str, user_id: str, role: TeamRole | str) -> dict[str, Any]:
        role_v = role.value if isinstance(role, TeamRole) else str(role)
        proj = self._data["projects"].setdefault(project_id, {"members": {}})
        proj["members"][user_id] = {"role": role_v}
        self._save()
        return proj["members"][user_id]

    def members(self, project_id: str) -> dict[str, Any]:
        return dict((self._data.get("projects") or {}).get(project_id, {}).get("members") or {})

    def can(self, project_id: str, user_id: str, action: str) -> bool:
        """Simple ACL matrix for architecture validation."""
        members = self.members(project_id)
        role = (members.get(user_id) or {}).get("role")
        if role == TeamRole.ADMIN.value:
            return True
        matrix = {
            TeamRole.TRANSLATOR.value: {"read", "write_translation"},
            TeamRole.EDITOR.value: {"read", "write_translation", "write_review"},
            TeamRole.DUB_DIRECTOR.value: {"read", "write_review", "write_decision", "export"},
            TeamRole.SOUND_ENGINEER.value: {"read", "write_audio", "export"},
            TeamRole.VIEWER.value: {"read"},
        }
        return action in matrix.get(role, set())


_TEAM: TeamService | None = None


def get_team_service(**kwargs: Any) -> TeamService:
    global _TEAM
    if _TEAM is None or kwargs:
        _TEAM = TeamService(**kwargs)
    return _TEAM
