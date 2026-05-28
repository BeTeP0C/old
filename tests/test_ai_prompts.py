from app.ai.prompts import SYSTEM_PROMPT, build_employee_explanation_prompt, build_messages


def test_build_messages_contains_system_prompt() -> None:
    messages = build_messages("user prompt")

    assert messages[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert messages[1] == {"role": "user", "content": "user prompt"}


def test_employee_prompt_contains_metrics_without_unrelated_fields() -> None:
    prompt = build_employee_explanation_prompt(
        {
            "employee": {"id": "employee-1", "full_name": "Ada"},
            "employee_metrics": {"risk_score": 0.75, "risk_level": "high"},
        }
    )

    assert "risk_score" in prompt
    assert "0.75" in prompt
    assert "employee_metrics" in prompt
    assert "email" not in prompt
