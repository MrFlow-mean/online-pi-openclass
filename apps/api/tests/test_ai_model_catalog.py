from app.models import AIModelSelection
from app.services import ai_model_catalog

TEST_USER_ID = "user_model_catalog"

def test_catalog_exposes_pi_compatible_and_shared_deepseek_text_models(monkeypatch) -> None:
    monkeypatch.setattr(ai_model_catalog, "pi_runtime_available", lambda: True)
    monkeypatch.setattr(
        ai_model_catalog,
        "pi_credentials_available",
        lambda **_kwargs: True,
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "disabled")
    monkeypatch.setenv("OPENAI_API_KEY", "disabled")
    monkeypatch.setenv("OPENAI_CODEX_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("AI_TEXT_PROVIDER", "google")
    monkeypatch.setenv("OPENCLASS_REALTIME_ENABLED", "true")
    monkeypatch.setenv("AI_TEXT_MODELS_JSON", '[{"provider":"deepseek","model":"legacy"}]')
    catalog = ai_model_catalog.build_model_catalog(TEST_USER_ID)

    assert [(option.provider, option.model) for option in catalog.text] == [
        ("openai_codex", "gpt-5.5"),
        ("openai_codex", "gpt-5.4"),
        ("openai_codex", "gpt-5.4-mini"),
        ("openai_codex", "gpt-5.3-codex-spark"),
        ("deepseek", "deepseek-v4-flash"),
        ("deepseek", "deepseek-v4-pro"),
    ]
    assert [option.label for option in catalog.text] == [
        "GPT 5.5",
        "GPT 5.4",
        "GPT 5.4 Mini",
        "GPT 5.3 Codex Spark",
        "DeepSeek V4 Flash",
        "DeepSeek V4 Pro",
    ]
    assert catalog.defaults["text"].provider == "openai_codex"
    assert catalog.defaults["text"].model == "gpt-5.4-mini"
    assert catalog.defaults["realtime"].provider == "openai"
    assert catalog.defaults["realtime"].model == "gpt-realtime-2.1"
    assert catalog.defaults["text"].agent_backend == "pi"
    assert [option.id for option in catalog.agent_backends["teaching"]] == [
        "pi",
        "codex",
    ]
    assert catalog.agent_backends["teaching"][0].enabled is True
    assert catalog.agent_backends["teaching"][1].enabled is False
    assert [option.id for option in catalog.agent_backends["source"]] == [
        "pi",
        "codex",
    ]
    assert catalog.agent_backends["source"][1].enabled is False
    assert len(catalog.realtime) == 2
    assert catalog.realtime[0].model == "gpt-realtime-2.1"
    assert catalog.realtime[0].default is True
    assert catalog.realtime[0].enabled is False
    assert catalog.realtime[0].configured is False
    assert [option.model for option in catalog.text if option.default] == ["gpt-5.4-mini"]
    assert all(
        option.enabled and option.configured
        for option in catalog.text
        if option.provider == "openai_codex"
    )
    assert all(
        not option.enabled and not option.configured
        for option in catalog.text
        if option.provider == "deepseek"
    )
    assert catalog.defaults["text"].reasoning_effort is None
    assert catalog.defaults["text"].service_tier is None
    assert [
        option.reasoning_effort
        for option in catalog.text[0].supported_reasoning_efforts
    ] == ["minimal", "low", "medium", "high", "xhigh"]
    assert [tier.id for tier in catalog.text[0].service_tiers] == ["priority"]
    assert all(
        option.supported_reasoning_efforts and option.service_tiers
        for option in catalog.text
        if option.provider == "openai_codex"
    )
    assert all(
        not option.supported_reasoning_efforts and not option.service_tiers
        for option in catalog.text
        if option.provider == "deepseek"
    )


def test_catalog_uses_pi_default_without_an_environment_override(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OPENAI_CODEX_MODEL", raising=False)
    monkeypatch.setattr(
        ai_model_catalog,
        "pi_credentials_available",
        lambda **_kwargs: True,
    )

    catalog = ai_model_catalog.build_model_catalog(TEST_USER_ID)

    assert catalog.defaults["text"].model == "gpt-5.5"
    assert catalog.defaults["text"].reasoning_effort is None
    assert catalog.text[0].default is True


def test_catalog_adds_configured_default_when_pi_curated_models_do_not_list_it(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_CODEX_MODEL", "custom-pi-model")
    monkeypatch.setattr(
        ai_model_catalog,
        "pi_credentials_available",
        lambda **_kwargs: True,
    )

    catalog = ai_model_catalog.build_model_catalog(TEST_USER_ID)

    assert catalog.text[0].provider == "openai_codex"
    assert catalog.text[0].model == "custom-pi-model"
    assert catalog.text[0].default is True


def test_catalog_disables_openai_options_until_pi_account_is_configured(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_CODEX_MODEL", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "disabled")
    monkeypatch.setenv("OPENAI_API_KEY", "disabled")
    monkeypatch.setattr(
        ai_model_catalog,
        "pi_credentials_available",
        lambda **_kwargs: False,
    )

    catalog = ai_model_catalog.build_model_catalog(TEST_USER_ID)

    assert catalog.text
    assert {option.provider for option in catalog.text} == {"openai_codex", "deepseek"}
    assert all(not option.enabled and not option.configured for option in catalog.text)
    assert len(catalog.realtime) == 2
    assert catalog.realtime[0].model == "gpt-realtime-2.1"
    assert catalog.realtime[0].enabled is False


def test_catalog_enables_openai_realtime_with_backend_key_and_flag(monkeypatch) -> None:
    monkeypatch.setenv("OPENCLASS_REALTIME_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("OPENAI_REALTIME_MODEL", "gpt-realtime-2.1-mini")
    monkeypatch.setattr(
        ai_model_catalog,
        "pi_credentials_available",
        lambda **_kwargs: False,
    )

    catalog = ai_model_catalog.build_model_catalog(TEST_USER_ID)

    assert catalog.defaults["realtime"].model == "gpt-realtime-2.1-mini"
    assert all(option.provider == "openai" for option in catalog.realtime)
    assert all(option.enabled and option.configured for option in catalog.realtime)
    assert [option.model for option in catalog.realtime if option.default] == ["gpt-realtime-2.1-mini"]
    assert all(option.transport == "openai_webrtc" for option in catalog.realtime)


def test_shared_deepseek_is_enabled_for_every_user_without_a_user_quota(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "server-shared-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    monkeypatch.setattr(
        ai_model_catalog,
        "pi_credentials_available",
        lambda **_kwargs: False,
    )

    guest_catalog = ai_model_catalog.build_model_catalog("guest_default")
    member_catalog = ai_model_catalog.build_model_catalog("user_member")

    for catalog in (guest_catalog, member_catalog):
        deepseek_options = [
            option for option in catalog.text if option.provider == "deepseek"
        ]
        assert deepseek_options
        assert all(option.enabled and option.configured for option in deepseek_options)
        assert catalog.defaults["text"].provider == "deepseek"
        assert catalog.defaults["text"].model == "deepseek-v4-flash"


def test_model_selection_defaults_to_pi_and_retains_codex_rollback_contract() -> None:
    default_selection = ai_model_catalog.default_text_selection()
    codex_selection = default_selection.model_copy(update={"agent_backend": "codex"})

    assert default_selection.agent_backend == "pi"
    assert codex_selection.agent_backend == "codex"


def test_text_selection_normalizes_legacy_backend_to_pi() -> None:
    selection = AIModelSelection(
        agent_backend="codex",
        provider="openai_codex",
        model="gpt-5.5",
    )

    resolved = ai_model_catalog.resolve_text_model_selection(
        selection,
        user_id="user_test",
    )

    assert resolved.agent_backend == "pi"


def test_teaching_pi_backend_is_enabled_when_runtime_is_installed(monkeypatch) -> None:
    monkeypatch.setattr(ai_model_catalog, "pi_runtime_available", lambda: True)

    options = ai_model_catalog._agent_backend_options()

    assert options["teaching"][0].enabled is True
    assert options["source"][0].enabled is True
    assert options["teaching"][1].enabled is False
    assert options["source"][1].enabled is False
