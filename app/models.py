from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class GiftContext(BaseModel):
    occasion: str = Field(min_length=1)
    budget_min: float = Field(ge=0)
    budget_max: float = Field(gt=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    country: str = Field(min_length=2)
    message_tone: str = Field(default="warm professional", min_length=2, max_length=50)

    @model_validator(mode="after")
    def valid_budget(self) -> "GiftContext":
        if self.budget_max < self.budget_min:
            raise ValueError("budget_max must be greater than or equal to budget_min")
        self.currency = self.currency.upper()
        return self


class LinkedInProfile(BaseModel):
    model_config = ConfigDict(extra="allow")

    headline: str = ""
    about: str = ""
    experience: list[Any] = Field(default_factory=list)
    recent_posts: list[str] = Field(default_factory=list)
    recent_comments: list[str] = Field(default_factory=list)
    engaged_topics: list[str] = Field(default_factory=list)


class RelationshipContext(BaseModel):
    model_config = ConfigDict(extra="allow")

    relationship_type: str = ""
    last_interaction: str = ""
    business_goal: str = ""


class ContactInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1)
    role: str = ""
    company: str = ""
    location: str = ""
    about: str = ""
    experience: list[Any] = Field(default_factory=list)
    recent_posts: list[str] = Field(default_factory=list)
    recent_comments: list[str] = Field(default_factory=list)
    engaged_topics: list[str] = Field(default_factory=list)
    linkedin_profile: LinkedInProfile | None = None
    relationship_context: RelationshipContext | None = None
    gift_context: GiftContext


class BatchRequest(BaseModel):
    contacts: list[ContactInput] = Field(min_length=1, max_length=100)


class ProfileSignals(BaseModel):
    strong_signals: list[str] = Field(default_factory=list)
    weak_signals: list[str] = Field(default_factory=list)
    signals_to_avoid: list[str] = Field(default_factory=list)


class Product(BaseModel):
    name: str
    url: HttpUrl
    price: float | None = None
    currency: str | None = None
    store: str = ""
    description: str = ""
    country: str | None = None


class RiskLevel(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class RankedGift(BaseModel):
    rank: int = Field(ge=1, le=3)
    gift_name: str
    product_url: HttpUrl
    store: str
    estimated_price: str
    why_this_gift: str
    personalisation_reasoning: str
    personalised_message: str = ""
    confidence_score: float = Field(ge=0, le=1)
    risk_level: RiskLevel
    assumptions: list[str] = Field(default_factory=list)


class ReviewAction(StrEnum):
    approve = "approve"
    reject = "reject"
    edit = "edit"
    regenerate = "regenerate"


class ReviewRequest(BaseModel):
    action: ReviewAction
    edited_gifts: list[RankedGift] | None = None
    feedback: str | None = None

    @model_validator(mode="after")
    def edits_required(self) -> "ReviewRequest":
        if self.action == ReviewAction.edit and not self.edited_gifts:
            raise ValueError("edited_gifts is required for the edit action")
        return self


class SearchTrace(BaseModel):
    queries_used: list[str]
    products_considered_count: int


class HumanReview(BaseModel):
    status: str
    available_actions: list[str] = ["approve", "reject", "edit", "regenerate"]


class FinalOutput(BaseModel):
    contact_name: str
    profile_signals: ProfileSignals
    search_trace: SearchTrace
    recommended_gifts: list[RankedGift]
    human_review: HumanReview
