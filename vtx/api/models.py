"""Validated request bodies shared by the HTTP modules."""
from typing import List, Optional

from pydantic import BaseModel, field_validator


class JobIn(BaseModel):
    url: str
    engine: Optional[str] = None     # service id from /api/engines; None = registry default
    workflow: Optional[List[str]] = None  # output template ids to run automatically after recognition
    llm_service: Optional[str] = None

    @field_validator("url")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("链接不能为空")
        if len(v) > 2000:
            raise ValueError("链接过长")
        return v


class SettingsIn(BaseModel):
    proxy_youtube: Optional[str] = None


class LLMSettingsIn(BaseModel):
    system_prompt: str

    @field_validator("system_prompt")
    @classmethod
    def bounded_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("规则不能为空")
        if len(value) > 20000:
            raise ValueError("规则过长")
        return value.strip()


class EngineIn(BaseModel):
    kind: Optional[str] = None
    label: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    allow_local: Optional[bool] = None
    region: Optional[str] = None
    timestamps: Optional[str] = None
    key: Optional[str] = None        # single-part secret
    secrets: Optional[dict] = None   # per-field secrets, e.g. {"secret_id": ..., "secret_key": ...}

    def fields(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if k not in ("key", "secrets") and v is not None}

    def secret_values(self) -> dict:
        return dict(self.secrets or {}, **({"key": self.key} if self.key else {}))


class LLMServiceIn(BaseModel):
    label: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    context: Optional[int] = None
    allow_local: Optional[bool] = None
    key_from: Optional[str] = None
    key: Optional[str] = None        # write-only

    def fields(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if k != "key" and v is not None}


class AIRunIn(BaseModel):
    output: str                      # output template id
    service: Optional[str] = None
    prompt: Optional[str] = None     # a one-off edited prompt, not saved
    source: str = "reading"          # free kind: reading | original
    text: Optional[str] = None       # current reading text from the page (after restores)

    @field_validator("source")
    @classmethod
    def known_source(cls, v: str) -> str:
        if v not in ("reading", "original"):
            raise ValueError("source 只能是 reading 或 original")
        return v


class OutputIn(BaseModel):
    name: Optional[str] = None
    kind: Optional[str] = None       # add only
    prompt: Optional[str] = None
    layouts: Optional[List[str]] = None
    copy_of: Optional[str] = None    # add only: start from this template

    def fields(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if k != "copy_of" and v is not None}


class TemplateIn(BaseModel):
    name: Optional[str] = None
    html: Optional[str] = None
    width: Optional[int] = None
    height_mode: Optional[str] = None
    group: Optional[str] = None      # preview / add without copy_of
    copy_of: Optional[str] = None    # add: start from this layout; preview: the layout being edited
    outputs: Optional[List[str]] = None

    def fields(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if k not in ("copy_of", "outputs") and v is not None}


class WorkflowIn(BaseModel):
    nodes: List[str] = []            # output template ids
    service: Optional[str] = None
