"""Server-side schema for the AI's structured output (PRD section 7)."""
from typing import Literal

from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    event_id: str = Field(description="ID of a timeline entry (real or inferred) that supports the statement")
    statement: str


class RecommendedAction(BaseModel):
    code: str
    explanation: str


class ProhibitedActionItem(BaseModel):
    code: str
    reason: str


class Diagnosis(BaseModel):
    summary: str
    root_cause: str
    supporting_evidence: list[EvidenceItem]
    contradictory_evidence: list[EvidenceItem] = Field(default_factory=list)
    alternative_explanations: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low", "inconclusive"]
    recommended_action: RecommendedAction
    prohibited_actions: list[ProhibitedActionItem] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)


class InvestigationResult(BaseModel):
    diagnosis: Diagnosis
    generated_by: str
    evidence_confidence: str
    llm_confidence: str | None = None
    validation: dict
