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
            "x github_pat_11FAKEfake0123456789_FAKEfake0123456789 y",
            "x [REDACTED] y",
            id="github-fine-grained-token",
        ),
        pytest.param(
            "x gho_FAKEfake0123456789FAKEfake0123456789 y",
            "x [REDACTED] y",
            id="github-oauth-token",
        ),
        pytest.param(
            "connect postgres://user:hunter2@db/x now",
            "connect postgres://user:[REDACTED]@db/x now",
            id="password-in-address",
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
    "name",
    ["password", "secret", "token", "api_key", "apikey", "secret_key", "access_key", "private_key"],
)
def test_a_text_value_after_a_name_ending_in_a_secret_word_is_masked_in_any_case(hook_module, name):
    text = f"MY_{name.upper()}=hunter2 and my{name}: hunter2"

    assert hook_module.mask_secrets(text) == f"MY_{name.upper()}=[REDACTED] and my{name}: [REDACTED]"


def test_name_value_json_and_yaml_forms_are_masked(hook_module):
    text = '{"password": "hunter2"}\nDB_PASSWORD: hunter2\napi_key: "abc123"\nAWS_SECRET_ACCESS_KEY=wJalr'

    assert hook_module.mask_secrets(text) == (
        '{"password": [REDACTED]}\nDB_PASSWORD: [REDACTED]\napi_key: [REDACTED]\nAWS_SECRET_ACCESS_KEY=[REDACTED]'
    )


@pytest.mark.parametrize(
    "text",
    [
        "input_token: 12",
        '{"input_token": 12, "has_token": true}',
        "MAX_SECRET=-3.5\nuse_token=False",
    ],
)
def test_numbers_and_true_false_stay_visible_under_a_secret_name(hook_module, text):
    assert hook_module.mask_secrets(text) == text


def test_a_value_starting_with_digits_is_still_masked(hook_module):
    assert hook_module.mask_secrets("DB_PASSWORD=2024-hunter2") == "DB_PASSWORD=[REDACTED]"


def test_structured_fields_are_masked_by_name_and_keep_numbers(hook_module):
    value = {"api_key": "abc123", "nested": [{"Client_Secret": "s3cret"}], "input_token": 12, "has_token": False}

    assert hook_module.mask_secrets(value) == {
        "api_key": "[REDACTED]",
        "nested": [{"Client_Secret": "[REDACTED]"}],
        "input_token": 12,
        "has_token": False,
    }


@pytest.mark.parametrize(
    "text",
    [
        "The build finished in three minutes.",
        "pip install sk-learn",
        "www.example.com",
        "token_type: bearer",
        "http://example.com:8080/path",
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
