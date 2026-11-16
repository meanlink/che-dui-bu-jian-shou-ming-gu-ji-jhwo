from pydantic import BaseModel, Field


class IntakeCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    source: str = Field(default='', max_length=200)
    rows: list = Field(min_length=1, max_length=5000)
    idempotency_key: str | None = Field(default=None, max_length=80)


class RowReplay(BaseModel):
    corrections: dict = Field(default_factory=dict)
    expected_seq: int | None = None


class RowAdjudicate(BaseModel):
    decision: str = Field(default='excluded', max_length=20)
    note: str = Field(min_length=2, max_length=300)
    expected_seq: int | None = None


class SettleRequest(BaseModel):
    expected_seq: int | None = None


class ConventionInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    config: dict
