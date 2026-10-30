"""Load and validate TeamPipeline YAML config."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from app.core.config import ROOT
from app.runtime.exceptions import RuntimeConfigError


class ModelRef(BaseModel):
    """Per-agent model binding. Secrets never appear here — only provider/model ids."""

    provider: str = "stub"  # stub | dashscope | openai
    model: str = "gateway-stub"


class MemberConfig(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=500)
    system_prompt: str = Field(min_length=1, max_length=4000)
    tools: list[str] = Field(default_factory=list)
    model: ModelRef = Field(default_factory=ModelRef)


class LeaderConfig(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    system_prompt: str = Field(min_length=1, max_length=4000)
    model: ModelRef = Field(default_factory=ModelRef)


class AssignStep(BaseModel):
    member: str
    prompt: str


class StubScript(BaseModel):
    leader_steps: list[dict[str, Any]] = Field(default_factory=list)
    member_replies: dict[str, str] = Field(default_factory=dict)


class TeamConfig(BaseModel):
    version: str = "1"
    timeout_ms: int = Field(default=20_000, ge=100, le=600_000)
    max_llm_calls: int = Field(default=20, ge=1, le=200)
    leader: LeaderConfig
    members: list[MemberConfig] = Field(min_length=1)
    stub_script: StubScript = Field(default_factory=StubScript)

    @field_validator("members")
    @classmethod
    def unique_member_names(cls, members: list[MemberConfig]) -> list[MemberConfig]:
        names = [m.name for m in members]
        if len(names) != len(set(names)):
            raise ValueError("member names must be unique")
        return members

    def validate_against_leader(self) -> None:
        if self.leader.name in {m.name for m in self.members}:
            raise RuntimeConfigError("member names must differ from leader name")
        known = {m.name for m in self.members}
        for step in self.stub_script.leader_steps:
            assign = step.get("assign")
            if assign and assign.get("member") not in known:
                raise RuntimeConfigError(
                    f"stub_script assigns unknown member {assign.get('member')!r}"
                )


def load_team_config(path: str | Path | None = None) -> TeamConfig:
    """Load team config from YAML. Raises RuntimeConfigError on invalid content."""
    cfg_path = Path(path) if path else ROOT / "config" / "agentscope_team.yaml"
    if not cfg_path.is_file():
        raise RuntimeConfigError(f"team config not found: {cfg_path}")
    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise RuntimeConfigError(f"invalid team YAML: {exc}") from exc
    try:
        team = TeamConfig.model_validate(raw)
        team.validate_against_leader()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeConfigError(f"invalid team config: {exc}") from exc
    return team
