from pydantic import BaseModel, Field


class CareerProject(BaseModel):
    project_name: str | None = None
    customer: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    role: str | None = None
    responsibilities: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    source_evidence: str | None = None


class CareerExtraction(BaseModel):
    person_name: str | None = None
    projects: list[CareerProject] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
