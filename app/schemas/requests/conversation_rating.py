from pydantic import BaseModel, ConfigDict, Field


class ConversationRatingSubmitBody(BaseModel):
    """POST /ratings/{token} — khách gửi điểm (public, không JWT)."""

    model_config = ConfigDict(extra="forbid")

    score: int = Field(..., ge=1, le=5, description="Điểm CSAT 1–5")
    comment: str | None = Field(
        default=None,
        max_length=2000,
        description="Nhận xét tùy chọn",
    )
