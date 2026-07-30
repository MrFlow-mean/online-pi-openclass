import json
from decimal import Decimal

import httpx
import pytest
from app.models import AIModelSelection
from app.services import ai_execution_adapter, ai_model_catalog, pi_agent_runtime
from app.services.codex_text_proxy import (
    CODEX_TEXT_PROXY_MODEL_IDS,
    CodexTextProxyClient,
    CodexTextProxyConfig,
)
from pydantic import BaseModel

TEST_USER_ID = "user_model_catalog"


@pytest.fixture(autouse=True)
def _no_personal_api_credentials(monkeypatch) -> None:
    monkeypatch.delenv("OPENCLASS_CODEX_TEXT_PROXY_ALLOWED_USER_IDS", raising=False)
    monkeypatch.delenv("OPENCLASS_CODEX_TEXT_PROXY_API_KEY", raising=False)
    monkeypatch.delenv("OPENCLASS_CODEX_TEXT_PROXY_API_KEY_FILE", raising=False)
    monkeypatch.delenv("OPENCLASS_CODEX_TEXT_PROXY_URL", raising=False)
    monkeypatch.setattr(
        ai_model_catalog,
        "pi_personal_api_configured",
        lambda **_kwargs: False,
    )


def test_owner_only_codex_text_proxy_models_are_catalogued_and_routed(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENCLASS_CODEX_TEXT_PROXY_ALLOWED_USER_IDS", TEST_USER_ID)
    monkeypatch.setenv("OPENCLASS_CODEX_TEXT_PROXY_API_KEY", "private-proxy-key")
    monkeypatch.setenv("OPENCLASS_CODEX_TEXT_PROXY_URL", "http://127.0.0.1:8317/v1")
    monkeypatch.setattr(
        ai_model_catalog,
        "pi_credentials_available",
        lambda **_kwargs: False,
    )

    catalog = ai_model_catalog.build_model_catalog(TEST_USER_ID)
    proxy_options = [
        option for option in catalog.text if option.model in CODEX_TEXT_PROXY_MODEL_IDS
    ]

    assert [option.model for option in proxy_options] == [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
    ]
    assert all(option.enabled and option.configured for option in proxy_options)
    assert all(
        option.access_method == "chatgpt_subscription" for option in proxy_options
    )
    assert [
        effort.reasoning_effort
        for effort in proxy_options[0].supported_reasoning_efforts
    ] == ["none", "low", "medium", "high", "xhigh", "max"]
    assert proxy_options[0].service_tiers == []
    assert catalog.defaults["text"].model == "gpt-5.6-sol"

    selection = ai_model_catalog.resolve_text_model_selection(
        AIModelSelection(
            provider="openai_codex",
            model="gpt-5.6-sol",
            access_method="chatgpt_subscription",
        ),
        user_id=TEST_USER_ID,
    )
    adapter = ai_execution_adapter.build_ai_execution_adapter(
        selection,
        owner_user_id=TEST_USER_ID,
    )
    assert isinstance(adapter, ai_execution_adapter.CodexTextProxyAIExecutionAdapter)
    assert adapter._selected_model_audit()["transport"] == "cliproxyapi"


def test_codex_text_proxy_models_are_hidden_and_rejected_for_other_users(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENCLASS_CODEX_TEXT_PROXY_ALLOWED_USER_IDS", TEST_USER_ID)
    monkeypatch.setenv("OPENCLASS_CODEX_TEXT_PROXY_API_KEY", "private-proxy-key")
    monkeypatch.setattr(
        ai_model_catalog,
        "pi_credentials_available",
        lambda **_kwargs: True,
    )
    other_user_id = "user_not_owner"

    catalog = ai_model_catalog.build_model_catalog(other_user_id)

    assert not CODEX_TEXT_PROXY_MODEL_IDS.intersection(
        option.model for option in catalog.text
    )
    forged = AIModelSelection(
        provider="openai_codex",
        model="gpt-5.6-sol",
        access_method="chatgpt_subscription",
    )
    with pytest.raises(RuntimeError, match="not allowed"):
        ai_model_catalog.resolve_text_model_selection(
            forged,
            user_id=other_user_id,
        )
    with pytest.raises(RuntimeError, match="not allowed"):
        ai_execution_adapter.build_ai_execution_adapter(
            forged,
            owner_user_id=other_user_id,
        )


class _ProxyProbe(BaseModel):
    value: str


def test_codex_text_proxy_uses_responses_api_for_structured_and_text_output(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENCLASS_CODEX_TEXT_PROXY_ALLOWED_USER_IDS", TEST_USER_ID)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)
        output = '{"value":"OK"}' if "text" in payload else "plain OK"
        return httpx.Response(
            200,
            json={
                "id": f"resp_{len(requests)}",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": output}],
                    }
                ],
                "usage": {"input_tokens": 3, "output_tokens": 2},
            },
        )

    http_client = httpx.Client(
        base_url="http://127.0.0.1:8317/v1/",
        transport=httpx.MockTransport(handler),
    )
    client = CodexTextProxyClient(
        owner_user_id=TEST_USER_ID,
        model="gpt-5.6-terra",
        reasoning_effort="high",
        config=CodexTextProxyConfig(
            api_key="private-proxy-key",
            base_url="http://127.0.0.1:8317/v1",
            max_output_tokens=512,
        ),
        client=http_client,
    )

    structured = client.parse(
        system_prompt="Return structured output.",
        user_prompt="Use OK.",
        schema=_ProxyProbe,
    )
    text_deltas: list[str] = []
    completed = client.complete_text(
        system_prompt="Answer plainly.",
        user_prompt="Say OK.",
        on_text_delta=text_deltas.append,
    )
    http_client.close()

    assert structured.output_parsed == _ProxyProbe(value="OK")
    assert completed.output_text == "plain OK"
    assert text_deltas == ["plain OK"]
    assert [request.url.path for request in requests] == [
        "/v1/responses",
        "/v1/responses",
    ]
    assert all(
        request.headers["authorization"] == "Bearer private-proxy-key"
        for request in requests
    )
    first_payload = json.loads(requests[0].content)
    assert first_payload["model"] == "gpt-5.6-terra"
    assert first_payload["reasoning"] == {"effort": "high"}
    assert first_payload["max_output_tokens"] == 512
    assert first_payload["text"]["format"]["type"] == "json_schema"


def test_catalog_exposes_pi_compatible_and_shared_deepseek_text_models(monkeypatch) -> None:
    monkeypatch.setenv("OPENCLASS_CREDIT_VALUE_PERCENT", "75")
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

    assert [
        (option.access_method, option.provider, option.model)
        for option in catalog.text
    ] == [
        ("chatgpt_subscription", "openai_codex", "gpt-5.5"),
        ("chatgpt_subscription", "openai_codex", "gpt-5.4"),
        ("chatgpt_subscription", "openai_codex", "gpt-5.4-mini"),
        ("chatgpt_subscription", "openai_codex", "gpt-5.3-codex-spark"),
        ("platform_credits", "deepseek", "deepseek-v4-flash"),
        ("platform_credits", "deepseek", "deepseek-v4-pro"),
        ("personal_api", "deepseek", "deepseek-v4-flash"),
        ("personal_api", "deepseek", "deepseek-v4-pro"),
    ]
    assert [option.label for option in catalog.text] == [
        "GPT 5.5",
        "GPT 5.4",
        "GPT 5.4 Mini",
        "GPT 5.3 Codex Spark",
        "DeepSeek V4 Flash",
        "DeepSeek V4 Pro",
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
    assert [
        option.input_price_credits_per_million
        for option in catalog.text
        if option.access_method == "platform_credits"
    ] == [19, 58]
    assert all(
        option.input_price_credits_per_million is None
        for option in catalog.text
        if option.access_method != "platform_credits"
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


def test_provider_policy_exposes_only_codex_models(monkeypatch) -> None:
    monkeypatch.setenv("OPENCLASS_TEXT_MODEL_PROVIDERS", "openai_codex")
    monkeypatch.setenv("OPENCLASS_REALTIME_MODEL_PROVIDERS", "openai_codex")
    monkeypatch.setenv("OPENCLASS_REALTIME_ENABLED", "true")
    monkeypatch.setenv("OPENCLASS_CODEX_REALTIME_ENABLED", "true")
    monkeypatch.setenv("OPENCLASS_CODEX_REALTIME_PROXY_API_KEY", "proxy-key")
    monkeypatch.setenv("OPENCLASS_CODEX_REALTIME_ALLOWED_USER_IDS", TEST_USER_ID)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "server-shared-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-platform-key")
    monkeypatch.setattr(
        ai_model_catalog,
        "pi_credentials_available",
        lambda **_kwargs: True,
    )
    monkeypatch.setattr(
        ai_model_catalog,
        "pi_personal_api_configured",
        lambda **_kwargs: True,
    )

    catalog = ai_model_catalog.build_model_catalog(TEST_USER_ID)

    assert {option.provider for option in catalog.text} == {"openai_codex"}
    assert {option.provider for option in catalog.realtime} == {"openai_codex"}
    assert catalog.defaults["text"].provider == "openai_codex"
    assert catalog.defaults["realtime"].provider == "openai_codex"
    with pytest.raises(RuntimeError, match="Unsupported text model provider"):
        ai_model_catalog.resolve_text_model_selection(
            AIModelSelection(
                provider="deepseek",
                model="deepseek-v4-flash",
                access_method="platform_credits",
            ),
            user_id=TEST_USER_ID,
        )


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
            option
            for option in catalog.text
            if option.provider == "deepseek"
            and option.access_method == "platform_credits"
        ]
        assert deepseek_options
        assert all(option.enabled and option.configured for option in deepseek_options)
        assert catalog.defaults["text"].provider == "deepseek"
        assert catalog.defaults["text"].model == "deepseek-v4-flash"
        assert catalog.defaults["text"].access_method == "platform_credits"


def test_openrouter_mapping_enables_platform_models_without_a_deepseek_key(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "disabled")
    monkeypatch.setenv("OPENCLASS_OPENROUTER_PROVISIONING_ENABLED", "true")
    monkeypatch.setenv("OPENROUTER_MANAGEMENT_API_KEY", "management-key")
    monkeypatch.setenv("OPENCLASS_CREDIT_VALUE_PERCENT", "75")
    monkeypatch.setenv(
        "OPENCLASS_OPENROUTER_MODEL_MAP_JSON",
        json.dumps(
            {
                "deepseek:deepseek-v4-flash": "deepseek/deepseek-chat",
                "deepseek:deepseek-v4-pro": "deepseek/deepseek-r1",
            }
        ),
    )
    monkeypatch.setattr(ai_model_catalog, "pi_runtime_available", lambda: True)
    monkeypatch.setattr(
        ai_model_catalog,
        "pi_credentials_available",
        lambda **_kwargs: False,
    )

    catalog = ai_model_catalog.build_model_catalog(TEST_USER_ID)

    platform = [
        option
        for option in catalog.text
        if option.provider == "deepseek"
        and option.access_method == "platform_credits"
    ]
    assert platform
    assert all(option.enabled and option.configured for option in platform)
    assert catalog.defaults["text"].access_method == "platform_credits"
    ai_model_catalog.apply_platform_input_prices(
        catalog,
        openrouter_config=ai_model_catalog.OpenRouterConfig.from_env(
            ai_model_catalog.DATABASE_PATH
        ),
        openrouter_prices={
            "deepseek/deepseek-chat": Decimal("0.098"),
            "deepseek/deepseek-r1": Decimal("0.435"),
        },
    )
    assert [option.input_price_credits_per_million for option in platform] == [14, 58]


def test_openrouter_prompt_prices_are_normalized_to_usd_per_million() -> None:
    assert ai_model_catalog._parse_openrouter_input_prices(
        [
            {
                "id": "provider/model",
                "pricing": {"prompt": "0.0000002"},
            },
            {"id": "missing-pricing"},
        ]
    ) == {"provider/model": Decimal("0.2")}


def test_personal_deepseek_key_enables_only_the_personal_api_route(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "disabled")
    monkeypatch.setattr(
        ai_model_catalog,
        "pi_credentials_available",
        lambda **_kwargs: False,
    )
    monkeypatch.setattr(
        ai_model_catalog,
        "pi_personal_api_configured",
        lambda **_kwargs: True,
    )

    catalog = ai_model_catalog.build_model_catalog(TEST_USER_ID)

    personal = [
        option
        for option in catalog.text
        if option.provider == "deepseek"
        and option.access_method == "personal_api"
    ]
    platform = [
        option
        for option in catalog.text
        if option.provider == "deepseek"
        and option.access_method == "platform_credits"
    ]
    assert all(option.enabled and option.configured for option in personal)
    assert all(not option.enabled and not option.configured for option in platform)
    assert catalog.defaults["text"].access_method == "personal_api"


def test_personal_api_key_is_private_user_scoped_and_removable(tmp_path) -> None:
    pi_agent_runtime.save_pi_personal_api_key(
        owner_user_id="user_a",
        provider="deepseek",
        api_key="sk-user-a",
        runtime_root=tmp_path,
    )

    assert pi_agent_runtime.pi_personal_api_configured(
        owner_user_id="user_a",
        provider="deepseek",
        runtime_root=tmp_path,
    )
    assert not pi_agent_runtime.pi_personal_api_configured(
        owner_user_id="user_b",
        provider="deepseek",
        runtime_root=tmp_path,
    )
    agent_dir = pi_agent_runtime._pi_user_agent_directory(
        owner_user_id="user_a",
        runtime_root=tmp_path,
    )
    auth_path = agent_dir / "auth.json"
    assert json.loads(auth_path.read_text(encoding="utf-8"))["deepseek"] == {
        "type": "api_key",
        "key": "sk-user-a",
    }
    assert agent_dir.stat().st_mode & 0o777 == 0o700
    assert auth_path.stat().st_mode & 0o777 == 0o600

    pi_agent_runtime.remove_pi_personal_api_key(
        owner_user_id="user_a",
        provider="deepseek",
        runtime_root=tmp_path,
    )
    assert not pi_agent_runtime.pi_personal_api_configured(
        owner_user_id="user_a",
        provider="deepseek",
        runtime_root=tmp_path,
    )


@pytest.mark.parametrize("api_key", ["!whoami", "$DEEPSEEK_API_KEY", "has whitespace"])
def test_personal_api_key_rejects_pi_resolution_syntax(
    api_key: str,
    tmp_path,
) -> None:
    with pytest.raises(ValueError):
        pi_agent_runtime.save_pi_personal_api_key(
            owner_user_id="user_a",
            provider="deepseek",
            api_key=api_key,
            runtime_root=tmp_path,
        )


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
