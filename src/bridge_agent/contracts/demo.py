"""Example capabilities used to demonstrate provider replacement without a model."""

from typing import Protocol

from bridge_agent.contracts.plugins import ServiceKey


class TextTransform(Protocol):
    def transform(self, text: str) -> str: ...


class Report(Protocol):
    def render(self, text: str) -> str: ...


TEXT_TRANSFORM = ServiceKey[TextTransform]("demo.text-transform")
REPORT = ServiceKey[Report]("demo.report")
