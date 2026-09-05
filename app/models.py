from pydantic import BaseModel, ConfigDict, Field

class Labels(BaseModel):
    alertname: str
    severity: str
    service: str
    model_config = ConfigDict(extra="allow")  # Allow extra fields in the Labels model

class Alert(BaseModel):
    id: str
    status: str
    labels: Labels
    annotations: dict[str, str]
    starts_at: str = Field(validation_alias="startsAt")
    value: str
    fingerprint: str
