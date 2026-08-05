from deepseek_cli.model_catalog import (
    MODEL_CHAT,
    MODEL_REASONER,
    resolve_model,
    resolve_provider_model,
    text_provider_models,
)


def test_documented_deepseek_model_ids():
    assert MODEL_CHAT == "deepseek-v4-flash"
    assert MODEL_REASONER == "deepseek-v4-pro"


def test_cli_model_resolution_is_strict_and_backwards_compatible():
    assert resolve_model("chat") == MODEL_CHAT
    assert resolve_model("deepseek-chat") == MODEL_CHAT
    assert resolve_model("reasoner") == MODEL_REASONER
    assert resolve_model("deepseek-reasoner") == MODEL_REASONER
    assert resolve_model("typo-model") is None


def test_anthropic_provider_model_matching_follows_documentation():
    assert resolve_provider_model("deepseek-v4-pro") == MODEL_REASONER
    assert resolve_provider_model("deepseek-v4-flash") == MODEL_CHAT
    assert resolve_provider_model("claude-opus-any-version") == MODEL_REASONER
    assert resolve_provider_model("claude-sonnet-any-version") == MODEL_CHAT
    assert resolve_provider_model("claude-haiku-any-version") == MODEL_CHAT
    assert resolve_provider_model("unknown-model") == MODEL_CHAT


def test_text_provider_models_show_the_actual_requested_model_ids():
    deepseek_models = text_provider_models("deepseek")
    assert [model.id for model in deepseek_models] == [
        MODEL_CHAT,
        MODEL_REASONER,
    ]
    assert all(model.id in model.label for model in deepseek_models)

    grsai_models = text_provider_models(
        "grsai", "gemini-3.1-flash-lite"
    )
    assert len(grsai_models) == 1
    assert grsai_models[0].id == MODEL_CHAT
    assert grsai_models[0].label == "GRS AI · gemini-3.1-flash-lite"
