"""Структура ответа LLM для второго прохода: события и локации."""

from pydantic import BaseModel, Field, field_validator

LOCATION_TYPES = ("city", "settlement", "building", "room", "nature", "region", "vehicle", "other")
LOCATION_ATTRIBUTE_CATEGORIES = (
    "geography", "architecture", "environment", "atmosphere", "time_context", "other"
)
EVENT_TYPES = ("plot", "character", "background")
EVENT_IMPORTANCE = ("major", "minor")


def _choice(allowed: tuple[str, ...], default: str):
    """Неизвестное значение enum → default (а не платный повтор запроса из-за валидации)."""

    def coerce(value):
        if isinstance(value, str) and value.strip().lower() in allowed:
            return value.strip().lower()
        return default

    return coerce


def _blank_to_none(value):
    if isinstance(value, str) and not value.strip():
        return None
    return value


class LocationAttributeData(BaseModel):
    category: str = "other"
    value: str
    evidence: str
    confidence: float = Field(default=0.8, ge=0, le=1)

    _category = field_validator("category", mode="before")(
        _choice(LOCATION_ATTRIBUTE_CATEGORIES, "other")
    )


class LocationMentionData(BaseModel):
    quote: str
    confidence: float = Field(default=0.9, ge=0, le=1)


class LocationData(BaseModel):
    name: str
    location_type: str = "other"
    aliases: list[str] = Field(default_factory=list)
    description: str | None = None
    attributes: list[LocationAttributeData] = Field(default_factory=list)
    mentions: list[LocationMentionData] = Field(default_factory=list)

    _clean = field_validator("description", mode="before")(_blank_to_none)
    _type = field_validator("location_type", mode="before")(_choice(LOCATION_TYPES, "other"))


class EventParticipantData(BaseModel):
    name: str
    role: str | None = None

    _clean = field_validator("role", mode="before")(_blank_to_none)


class EventData(BaseModel):
    title: str
    description: str
    event_type: str = "plot"
    importance: str = "minor"
    is_turning_point: bool = False
    is_climax: bool = False
    participants: list[EventParticipantData] = Field(default_factory=list)
    location_name: str | None = None
    time_text: str | None = None
    cause: str | None = None
    consequences: str | None = None
    evidence: str
    confidence: float = Field(default=0.8, ge=0, le=1)

    _clean = field_validator(
        "location_name", "time_text", "cause", "consequences", mode="before"
    )(_blank_to_none)
    _type = field_validator("event_type", mode="before")(_choice(EVENT_TYPES, "plot"))
    _importance = field_validator("importance", mode="before")(_choice(EVENT_IMPORTANCE, "minor"))


class StoryElementsResult(BaseModel):
    locations: list[LocationData] = Field(default_factory=list)
    events: list[EventData] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
