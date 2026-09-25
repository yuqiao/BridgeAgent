"""A consumer independent of provider selection and host internals."""

from bridge_agent.contracts.demo import Report


def render_report(report: Report, text: str) -> str:
    return report.render(text)
