"""Environment input and strict configuration at the composition boundary."""

from pathlib import Path

import pytest

from bridge_agent.bootstrap.agent import read_environment


def test_yaml_accepts_600_second_model_and_runtime_timeouts(tmp_path: Path) -> None:
    from bridge_agent.bootstrap.agent import agent_catalog

    path = tmp_path / "agent.yaml"
    path.write_text("""version: 1
plugins:
  - name: runtime.langchain
    config: {timeout_seconds: 600}
  - name: model.openai
    config: {timeout_seconds: 600}
  - name: tools.arithmetic
  - name: checkpoint.memory
""")
    agent_catalog(
        environment={
            "OPENAI_BASE_URL": "http://127.0.0.1:1/v1",
            "OPENAI_API_KEY": "test-only",
            "OPENAI_MODEL": "local-model",
        }
    ).load(path)


def test_env_file_is_explicit_and_process_values_win(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / ".env"
    path.write_text("OPENAI_MODEL=file-model\nOPENAI_API_KEY='literal-${TOKEN}'\n")
    monkeypatch.setenv("OPENAI_MODEL", "process-model")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    values = read_environment(path)
    assert values["OPENAI_MODEL"] == "process-model"
    assert values["OPENAI_API_KEY"] == "literal-${TOKEN}"
    assert "OPENAI_API_KEY" not in read_environment(None)


def test_explicit_missing_env_file_reports_configuration_error(tmp_path: Path) -> None:
    from bridge_agent.contracts.errors import ConfigurationError

    with pytest.raises(ConfigurationError, match="environment file"):
        read_environment(tmp_path / "missing.env")


def test_explicit_override_uses_file_values_without_mutating_process(
    tmp_path: Path, monkeypatch
) -> None:
    import os

    path = tmp_path / ".env"
    path.write_text("OPENAI_API_KEY=file-credential\n")
    monkeypatch.setenv("OPENAI_API_KEY", "process-credential")
    assert read_environment(path, override=True)["OPENAI_API_KEY"] == "file-credential"
    assert os.environ["OPENAI_API_KEY"] == "process-credential"


@pytest.mark.parametrize(
    "config",
    [
        "{max_model_calls: 0}",
        "{timeout_seconds: -1}",
        "{max_model_calls: true}",
        "{unknown: 1}",
    ],
)
def test_runtime_configuration_rejects_invalid_limits(
    tmp_path: Path, config: str
) -> None:
    from bridge_agent.bootstrap.agent import agent_catalog
    from bridge_agent.contracts.errors import ConfigurationError

    path = tmp_path / "invalid.yaml"
    path.write_text(
        "version: 1\nplugins:\n  - name: runtime.langchain\n    config: " + config
    )
    with pytest.raises(ConfigurationError, match="runtime.langchain"):
        agent_catalog(environment={}).load(path)
