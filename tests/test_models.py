import pytest
from pydantic import ValidationError

from app.models import ContactInput, GiftContext, ReviewRequest


def test_budget_range_is_validated():
    with pytest.raises(ValidationError):
        GiftContext(
            occasion="thanks", budget_min=100, budget_max=50,
            currency="INR", country="India",
        )


def test_edit_requires_gifts():
    with pytest.raises(ValidationError):
        ReviewRequest(action="edit")


def test_contact_accepts_enriched_fields():
    contact = ContactInput(
        name="Aarav", custom_profile_field="kept",
        gift_context={
            "occasion": "thanks", "budget_min": 10, "budget_max": 20,
            "currency": "usd", "country": "US",
        },
    )
    assert contact.gift_context.currency == "USD"
    assert contact.model_dump()["custom_profile_field"] == "kept"


def test_contact_accepts_official_nested_schema():
    contact = ContactInput(
        name="Aarav Mehta",
        linkedin_profile={
            "headline": "VP Sales at B2B SaaS company",
            "about": "Leads enterprise SaaS sales.",
            "recent_posts": ["Posted about cricket strategy and sales leadership."],
            "engaged_topics": ["cricket", "SaaS growth"],
        },
        relationship_context={
            "relationship_type": "prospect champion",
            "business_goal": "Thank for supporting a pilot.",
        },
        gift_context={
            "occasion": "pilot thank-you",
            "budget_min": 3000,
            "budget_max": 5000,
            "currency": "INR",
            "country": "India",
            "message_tone": "warm professional",
        },
    )
    assert contact.linkedin_profile.about.startswith("Leads")
    assert contact.relationship_context.business_goal.startswith("Thank")
