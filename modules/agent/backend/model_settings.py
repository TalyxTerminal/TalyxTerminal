"""Model and connection fields shared by the settings page and public actions."""
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator
from talyx_sdk.declare import Model


class ConnectionFields(Model):
    id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=160)
    providerId: str = Field(min_length=1, max_length=160)
    kind: Literal["official", "relay", "local"]
    protocol: Literal["openai", "anthropic", "google", "ollama"]
    baseUrl: str
    site: Literal["domestic", "international"] | None = None


class ModelConfig(Model):
    id: str = Field(min_length=1, max_length=160)
    connectionId: str = Field(min_length=1)
    model: str = Field(min_length=1, max_length=256)
    profileProvider: str | None = Field(default=None, min_length=1, max_length=160)
    profileModel: str | None = None
    contextWindow: int | None = Field(default=None, gt=0)
    maxOutputTokens: int | None = Field(default=None, gt=0)
    tools: bool | None = None
    images: bool | None = None
    pdf: bool | None = None
    # Whether the model can think, as a local server reports it (Ollama's thinking capability).
    thinking: bool | None = None
    price: dict | None = None
    priceMultiplier: str = "1"
    showPrice: bool = True
    searchMode: Literal["provider", "talyx"] = "talyx"

    @field_validator("priceMultiplier")
    @classmethod
    def check_multiplier(cls, value):
        try:
            amount = Decimal(value)
            if not amount.is_finite() or amount < 0:
                raise ValueError("Multiplier must be finite and non-negative")
        except ArithmeticError as error:
            raise ValueError("Multiplier must be numeric") from error
        return value
