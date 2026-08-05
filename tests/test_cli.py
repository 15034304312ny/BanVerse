from io import StringIO

from deepseek_cli.cli import main


def test_missing_api_key_returns_error_without_reading_other_names():
    output = StringIO()

    exit_code = main(
        environ={
            "OPENAI_API_KEY": "must-not-be-used",
            "ANTHROPIC_API_KEY": "must-also-not-be-used",
        },
        output=output,
    )

    assert exit_code == 2
    assert "DEEPSEEK_API_KEY" in output.getvalue()
