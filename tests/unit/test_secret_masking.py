from __future__ import annotations

import copy

import pytest


PRIVATE_KEY_BLOCK = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEAfakefakefakefakefakefake\n"
    "fakefakefakefakefakefakefakefakefakefake\n"
    "-----END RSA PRIVATE KEY-----"
)
FAKE_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJmYWtlIn0.ZmFrZXNpZ25hdHVyZQ"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            f"before\n{PRIVATE_KEY_BLOCK}\nafter",
            "before\n[REDACTED]\nafter",
            id="private-key-block",
        ),
        pytest.param(
            'curl -H "Authorization: Bearer abc123" x',
            'curl -H "Authorization: Bearer [REDACTED]" x',
            id="bearer-header-line",
        ),
        pytest.param(
            '{"headers": {"Authorization": "Bearer abc123"}}',
            '{"headers": {"Authorization": "Bearer [REDACTED]"}}',
            id="bearer-json-text",
        ),
        pytest.param(
            "{'authorization': 'bearer abc123'}",
            "{'authorization': 'bearer [REDACTED]'}",
            id="bearer-python-text",
        ),
        pytest.param(f"token {FAKE_JWT} end", "token [REDACTED] end", id="jwt"),
        pytest.param(
            "key sk-ant-api03-FAKEfakeFAKEfake0123456789 end",
            "key [REDACTED] end",
            id="sk-key",
        ),
        pytest.param("aws --key AKIAIOSFODNN7EXAMPLE now", "aws --key [REDACTED] now", id="aws-access-key"),
        pytest.param(
            "x ghp_FAKEfake0123456789_FAKEfake0123456789 y",
            "x [REDACTED] y",
            id="github-token",
        ),
        pytest.param(
            "slack xoxb-FAKE-test-token z",
            "slack [REDACTED] z",
            id="slack-token",
        ),
        pytest.param("login password=hunter2 now", "login password=[REDACTED] now", id="password-value"),
    ],
)
def test_each_kind_of_secret_is_replaced_by_the_marker(hook_module, text, expected):
    assert hook_module.mask_secrets(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "postgres://app?password=s3cret&sslmode=require",
            "postgres://app?password=[REDACTED]&sslmode=require",
        ),
        ('DB_PASSWORD="two words"', "DB_PASSWORD=[REDACTED]"),
        ("PASSWORD=abc\nnext line", "PASSWORD=[REDACTED]\nnext line"),
    ],
)
def test_password_value_ends_at_space_ampersand_or_line_end_and_quotes_go_whole(hook_module, text, expected):
    assert hook_module.mask_secrets(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "The build finished in three minutes.",
        "pip install sk-learn",
        "www.example.com",
        "password: x",
    ],
)
def test_text_without_secrets_comes_back_unchanged(hook_module, text):
    assert hook_module.mask_secrets(text) == text


def test_structured_data_is_masked_in_a_copy_and_keeps_its_shape(hook_module):
    value = {
        "headers": {"Authorization": "Bearer abc123", "Accept": "text/plain"},
        "args": ["--key", "AKIAIOSFODNN7EXAMPLE"],
        "n": 3,
        "ok": True,
        "x": None,
    }
    original = copy.deepcopy(value)

    masked = hook_module.mask_secrets(value)

    assert masked == {
        "headers": {"Authorization": "Bearer [REDACTED]", "Accept": "text/plain"},
        "args": ["--key", "[REDACTED]"],
        "n": 3,
        "ok": True,
        "x": None,
    }
    assert value == original
