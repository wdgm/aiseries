from pydantic import BaseModel, Field


class CharacterMentionData(BaseModel):
    quote: str
    context: str
    confidence: float = Field(default=0.9, ge=0, le=1)


class CharacterAttributeData(BaseModel):
    category: str
    value: str
    evidence: str
    confidence: float = Field(ge=0, le=1)


class CharacterRelationshipData(BaseModel):
    target_character_name: str
    relationship_type: str
    description: str
    evidence: str
    confidence: float = Field(ge=0, le=1)


class CharacterData(BaseModel):
    name: str
    description: str | None = None

    character_type: str = "person"
    importance: str = "secondary"

    aliases: list[str] = Field(default_factory=list)
    attributes: list[CharacterAttributeData] = Field(default_factory=list)
    mentions: list[CharacterMentionData] = Field(default_factory=list)
    relationships: list[CharacterRelationshipData] = Field(default_factory=list)


class BookAnalysisResult(BaseModel):
    characters: list[CharacterData] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)