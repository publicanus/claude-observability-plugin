"""Pins the data-residency gate: EU by default, non-EU Langfuse Cloud refused.

A default host decides where an unconfigured install's session data lands, and
"unconfigured" is the common case. Defaulting to a US endpoint means a European
operator's turns, tool calls and /hurt comments cross a jurisdiction with no
symptom other than traces not showing up where expected — so the default is EU
and the other Langfuse Cloud regions are refused outright rather than merely
defaulted away from (a default only steers; an explicitly set US host would
still ship everything).

Self-hosted hosts stay allowed: nothing in a URL says where the machine behind
it stands, so refusing them would break a documented use case while proving
nothing about residency.
"""

import json
from typing import Any

import pytest


EU = "https://cloud.langfuse.com"


@pytest.fixture(autouse=True)
def clear_langfuse_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's own Langfuse env must not decide these outcomes."""
    for name in (
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_BASE_URL",
        "LANGFUSE_USER_ID",
        "CC_LANGFUSE_PUBLIC_KEY",
        "CC_LANGFUSE_SECRET_KEY",
        "CC_LANGFUSE_BASE_URL",
        "CC_LANGFUSE_TRACE_SEED",
    ):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"CLAUDE_PLUGIN_OPTION_{name}", raising=False)


@pytest.fixture
def configured_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")


# ----------------- the default -----------------

def test_unset_base_url_defaults_to_langfuse_cloud_eu(hook_module: Any, configured_keys):
    config = hook_module.get_langfuse_config()

    assert config is not None
    assert config.host == EU


def test_the_declared_eu_host_constant_is_the_eu_cloud_host(hook_module: Any):
    assert hook_module.EU_CLOUD_HOST == EU


# ----------------- what is refused -----------------

@pytest.mark.parametrize(
    "host",
    [
        "https://us.cloud.langfuse.com",
        "https://hipaa.cloud.langfuse.com",
        "https://jp.cloud.langfuse.com",
    ],
)
def test_non_eu_cloud_hosts_yield_no_usable_config(
    hook_module: Any, isolated_hook_state, configured_keys, monkeypatch, host: str
):
    monkeypatch.setenv("LANGFUSE_BASE_URL", host)

    # No config means no client, no probe, no hurt attachment: the refusal
    # sits on the one value every send path in this process is built from.
    assert hook_module.get_langfuse_config() is None


@pytest.mark.parametrize(
    "written, region_fragment",
    [
        ("https://us.cloud.langfuse.com", "US"),
        ("https://US.Cloud.Langfuse.com", "US"),
        ("https://us.cloud.langfuse.com/", "US"),
        ("https://us.cloud.langfuse.com:443", "US"),
        ("us.cloud.langfuse.com", "US"),
        ("  https://us.cloud.langfuse.com  ", "US"),
        ("https://hipaa.cloud.langfuse.com", "HIPAA"),
        ("https://jp.cloud.langfuse.com", "Japan"),
    ],
)
def test_a_non_eu_cloud_host_is_recognised_however_it_is_written(
    hook_module: Any, written: str, region_fragment: str
):
    region = hook_module.non_eu_cloud_region(written)

    assert region is not None
    assert region_fragment in region


# ----------------- what stays allowed -----------------

@pytest.mark.parametrize(
    "host",
    [
        EU,
        "https://cloud.langfuse.com/",
        # Self-hosted: the URL says nothing about where the machine stands,
        # so the plugin cannot and does not judge it.
        "https://langfuse.internal.example.com",
        "http://localhost:3000",
        # Not the US cloud host — a self-hosted instance that merely reads
        # like one must not be caught by a substring match.
        "https://us.cloud.langfuse.com.example.com",
        "https://not-us.cloud.langfuse.com",
    ],
)
def test_eu_cloud_and_self_hosted_hosts_are_allowed(
    hook_module: Any, configured_keys, monkeypatch, host: str
):
    monkeypatch.setenv("LANGFUSE_BASE_URL", host)

    assert hook_module.non_eu_cloud_region(host) is None
    config = hook_module.get_langfuse_config()
    assert config is not None
    assert config.host == host


# ----------------- every channel the host can arrive through -----------------

def test_the_secondary_env_var_is_gated_too(
    hook_module: Any, isolated_hook_state, configured_keys, monkeypatch
):
    """CC_LANGFUSE_BASE_URL is a second way to set the same value; a gate on
    only the primary one would be a documented way around it."""
    monkeypatch.setenv("CC_LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com")

    assert hook_module.get_langfuse_config() is None


def test_the_install_wizard_value_is_gated_too(
    hook_module: Any, isolated_hook_state, configured_keys, monkeypatch
):
    """userConfig arrives as CLAUDE_PLUGIN_OPTION_<NAME>; the gate sits on the
    resolved host, so no configuration channel bypasses it."""
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_LANGFUSE_BASE_URL", "https://jp.cloud.langfuse.com")

    assert hook_module.get_langfuse_config() is None


# ----------------- what the user is told -----------------

def test_the_refusal_names_the_host_the_region_and_the_way_out(hook_module: Any):
    message = hook_module.format_residency_refusal("https://us.cloud.langfuse.com")

    assert message is not None
    # The host actually configured, not a generic "misconfiguration".
    assert "https://us.cloud.langfuse.com" in message
    assert "US" in message
    # States the outcome: refused, nothing sent — not "failed to send".
    assert "no traces" in message
    # Names the EU host to set, and the self-hosted escape hatch.
    assert EU in message
    assert "self-hosted" in message
    # Same restart caveat every other repair recipe in this hook carries:
    # env vars are frozen at launch, so editing them now changes nothing.
    assert "restart Claude Code" in message
    assert "frozen" in message


def test_no_refusal_for_an_allowed_host(hook_module: Any):
    assert hook_module.format_residency_refusal(EU) is None
    assert hook_module.format_residency_refusal("https://langfuse.internal.example.com") is None


def test_residency_refusal_is_silent_on_an_install_with_no_keys(
    hook_module: Any, monkeypatch
):
    """An install with no credentials sends nothing anyway and is documented
    to have zero impact — it must not start printing warnings."""
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com")

    assert hook_module.residency_refusal() is None


def test_residency_refusal_speaks_up_once_the_plugin_is_actually_configured(
    hook_module: Any, configured_keys, monkeypatch
):
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com")

    message = hook_module.residency_refusal()

    assert message is not None
    assert "us.cloud.langfuse.com" in message


# ----------------- end to end through main() -----------------

def test_main_refuses_loudly_and_never_builds_a_client(
    hook_module: Any, isolated_hook_state, configured_keys, monkeypatch, capsys
):
    """A refusal is visible and harmless: the session is warned, nothing is
    sent, and Claude Code is not disturbed (exit 0, like every other failure
    path in this hook)."""
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com")
    monkeypatch.setattr(
        hook_module,
        "create_langfuse_client",
        lambda config: pytest.fail("a refused host must never reach the SDK"),
    )
    monkeypatch.setattr(
        hook_module,
        "LangfuseClient",
        lambda config, timeout=30: pytest.fail("a refused host must never be contacted"),
    )

    rc = hook_module.main()

    assert rc == 0
    warning = json.loads(capsys.readouterr().out)["systemMessage"]
    assert "us.cloud.langfuse.com" in warning
    assert EU in warning


def test_main_stays_silent_on_an_allowed_host_with_no_keys(
    hook_module: Any, isolated_hook_state, monkeypatch, capsys
):
    rc = hook_module.main()

    assert rc == 0
    assert capsys.readouterr().out == ""
