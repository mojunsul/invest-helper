from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

UUID_RE = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
SessionId = Annotated[str, StringConstraints(pattern=UUID_RE)]  # chat 본문·logs 경로 공용


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: SessionId
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]

    @field_validator("session_id")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()
