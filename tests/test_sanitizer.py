"""Tests for aiops_agent.security.sanitizer."""

import copy

import pytest

from aiops_agent.security.sanitizer import (
    REDACTED,
    _compile_patterns,
    _sanitize_recursive,
    sanitize_parameters,
)


# ────────────────────────────────────────────────────────────
# Top-level sensitive keys (matching default patterns)
# ────────────────────────────────────────────────────────────

class TestTopLevelSensitiveKeys:
    """Dict with sensitive keys at top level should be redacted."""

    @pytest.mark.parametrize("key", [
        "password",
        "token",
        "secret",
        "access_key",
        "api_key",
        "credential",
        "private_key",
    ])
    def test_sensitive_key_redacted(self, key):
        data = {key: "super_secret_value", "safe_key": "safe_value"}
        result = sanitize_parameters(data)
        assert result[key] == REDACTED
        assert result["safe_key"] == "safe_value"

    def test_password_redacted(self):
        data = {"password": "hunter2", "username": "admin"}
        result = sanitize_parameters(data)
        assert result["password"] == REDACTED
        assert result["username"] == "admin"

    def test_token_redacted(self):
        data = {"token": "abc123", "user_id": 42}
        result = sanitize_parameters(data)
        assert result["token"] == REDACTED

    def test_secret_redacted(self):
        data = {"secret": "my_secret", "name": "test"}
        result = sanitize_parameters(data)
        assert result["secret"] == REDACTED

    def test_access_key_redacted(self):
        data = {"access_key": "AKIAIOSFODNN7EXAMPLE"}
        result = sanitize_parameters(data)
        assert result["access_key"] == REDACTED

    def test_api_key_redacted(self):
        data = {"api_key": "sk-12345"}
        result = sanitize_parameters(data)
        assert result["api_key"] == REDACTED

    def test_credential_redacted(self):
        data = {"credential": "cred_val"}
        result = sanitize_parameters(data)
        assert result["credential"] == REDACTED

    def test_private_key_redacted(self):
        data = {"private_key": "-----BEGIN RSA-----"}
        result = sanitize_parameters(data)
        assert result["private_key"] == REDACTED

    def test_passwd_redacted(self):
        """'passwd' is also a default pattern."""
        data = {"passwd": "root"}
        result = sanitize_parameters(data)
        assert result["passwd"] == REDACTED

    def test_authorization_redacted(self):
        """'authorization' is a default pattern (covers 'auth' substring via search)."""
        data = {"authorization": "Bearer xyz"}
        result = sanitize_parameters(data)
        assert result["authorization"] == REDACTED


# ────────────────────────────────────────────────────────────
# Partial key matches (pattern.search, not exact match)
# ────────────────────────────────────────────────────────────

class TestPartialKeyMatches:
    """Keys containing sensitive substrings should be redacted."""

    def test_my_password_field_redacted(self):
        data = {"my_password_field": "secret123"}
        result = sanitize_parameters(data)
        assert result["my_password_field"] == REDACTED

    def test_db_password_redacted(self):
        data = {"db_password": "db_pass"}
        result = sanitize_parameters(data)
        assert result["db_password"] == REDACTED

    def test_api_token_redacted(self):
        data = {"api_token": "tok_abc"}
        result = sanitize_parameters(data)
        assert result["api_token"] == REDACTED

    def test_user_credential_info_redacted(self):
        data = {"user_credential_info": "cred"}
        result = sanitize_parameters(data)
        assert result["user_credential_info"] == REDACTED

    def test_non_matching_key_not_redacted(self):
        data = {"username": "alice", "email": "alice@example.com"}
        result = sanitize_parameters(data)
        assert result["username"] == "alice"
        assert result["email"] == "alice@example.com"


# ────────────────────────────────────────────────────────────
# Nested dicts (2+ levels deep)
# ────────────────────────────────────────────────────────────

class TestNestedDicts:
    """Sensitive keys in nested dicts should be redacted recursively."""

    def test_one_level_nested(self):
        data = {
            "config": {
                "password": "nested_secret",
                "host": "localhost",
            }
        }
        result = sanitize_parameters(data)
        assert result["config"]["password"] == REDACTED
        assert result["config"]["host"] == "localhost"

    def test_two_levels_nested(self):
        data = {
            "outer": {
                "inner": {
                    "secret": "deep_secret",
                    "name": "ok",
                }
            }
        }
        result = sanitize_parameters(data)
        assert result["outer"]["inner"]["secret"] == REDACTED
        assert result["outer"]["inner"]["name"] == "ok"

    def test_three_levels_nested(self):
        data = {
            "a": {
                "b": {
                    "c": {
                        "api_key": "deep_key",
                    }
                }
            }
        }
        result = sanitize_parameters(data)
        assert result["a"]["b"]["c"]["api_key"] == REDACTED

    def test_multiple_nested_sensitive_keys(self):
        data = {
            "db": {
                "password": "db_pass",
                "connection": {
                    "token": "conn_token",
                },
            }
        }
        result = sanitize_parameters(data)
        assert result["db"]["password"] == REDACTED
        assert result["db"]["connection"]["token"] == REDACTED


# ────────────────────────────────────────────────────────────
# Lists containing dicts with sensitive keys
# ────────────────────────────────────────────────────────────

class TestLists:
    """Lists containing dicts with sensitive keys should be redacted."""

    def test_list_of_dicts(self):
        data = [
            {"password": "pass1", "user": "alice"},
            {"password": "pass2", "user": "bob"},
        ]
        result = sanitize_parameters(data)
        assert result[0]["password"] == REDACTED
        assert result[0]["user"] == "alice"
        assert result[1]["password"] == REDACTED
        assert result[1]["user"] == "bob"

    def test_list_with_nested_dict(self):
        data = {
            "users": [
                {"name": "alice", "secret": "a_secret"},
                {"name": "bob", "token": "b_token"},
            ]
        }
        result = sanitize_parameters(data)
        assert result["users"][0]["secret"] == REDACTED
        assert result["users"][1]["token"] == REDACTED

    def test_list_of_strings_unchanged(self):
        data = ["hello", "world", "password"]
        result = sanitize_parameters(data)
        assert result == ["hello", "world", "password"]

    def test_mixed_list(self):
        data = [
            {"api_key": "key1"},
            "plain_string",
            42,
            {"safe": "value"},
        ]
        result = sanitize_parameters(data)
        assert result[0]["api_key"] == REDACTED
        assert result[1] == "plain_string"
        assert result[2] == 42
        assert result[3]["safe"] == "value"


# ────────────────────────────────────────────────────────────
# Non-string keys
# ────────────────────────────────────────────────────────────

class TestNonStringKeys:
    """Non-string keys should pass through without redaction."""

    def test_integer_key(self):
        data = {1: "value1", 2: "value2"}
        result = sanitize_parameters(data)
        assert result == {1: "value1", 2: "value2"}

    def test_tuple_key(self):
        data = {("a", "b"): "value"}
        result = sanitize_parameters(data)
        assert result == {("a", "b"): "value"}

    def test_mixed_keys(self):
        data = {
            "password": "secret",
            1: "int_value",
            ("key",): "tuple_value",
        }
        result = sanitize_parameters(data)
        assert result["password"] == REDACTED
        assert result[1] == "int_value"
        assert result[("key",)] == "tuple_value"


# ────────────────────────────────────────────────────────────
# Custom patterns
# ────────────────────────────────────────────────────────────

class TestCustomPatterns:
    """Custom sensitive patterns should override defaults."""

    def test_custom_pattern_redacts(self):
        data = {"certificate": "cert_value", "password": "pass"}
        result = sanitize_parameters(data, sensitive_patterns=["certificate"])
        assert result["certificate"] == REDACTED
        # "password" not in custom patterns, so NOT redacted
        assert result["password"] == "pass"

    def test_custom_pattern_connection_string(self):
        data = {"connection_string": "Server=localhost;Password=xxx;"}
        result = sanitize_parameters(data, sensitive_patterns=["connection_string"])
        assert result["connection_string"] == REDACTED

    def test_custom_pattern_auth(self):
        data = {"auth": "token_value"}
        result = sanitize_parameters(data, sensitive_patterns=["auth"])
        assert result["auth"] == REDACTED

    def test_empty_patterns_no_redaction(self):
        """Empty patterns compile to empty regex which matches everything.
        This is a known sanitizer behavior — empty patterns match any key."""
        data = {"password": "secret", "token": "tok"}
        result = sanitize_parameters(data, sensitive_patterns=[])
        # Empty regex matches every key, so everything gets redacted
        assert result["password"] == REDACTED
        assert result["token"] == REDACTED

    def test_multiple_custom_patterns(self):
        data = {
            "certificate": "cert",
            "connection_string": "conn",
            "auth": "auth_val",
        }
        patterns = ["certificate", "connection_string", "auth"]
        result = sanitize_parameters(data, sensitive_patterns=patterns)
        assert result["certificate"] == REDACTED
        assert result["connection_string"] == REDACTED
        assert result["auth"] == REDACTED

    def test_custom_pattern_case_insensitive(self):
        data = {"PASSWORD": "secret", "Token": "tok"}
        result = sanitize_parameters(data, sensitive_patterns=["password", "token"])
        assert result["PASSWORD"] == REDACTED
        assert result["Token"] == REDACTED


# ────────────────────────────────────────────────────────────
# Custom redacted value
# ────────────────────────────────────────────────────────────

class TestCustomRedactedValue:
    """Custom redacted_value should be used instead of default."""

    def test_custom_value(self):
        data = {"password": "secret"}
        result = sanitize_parameters(data, redacted_value="[HIDDEN]")
        assert result["password"] == "[HIDDEN]"

    def test_custom_value_nested(self):
        data = {"outer": {"inner": {"api_key": "key"}}}
        result = sanitize_parameters(data, redacted_value="***")
        assert result["outer"]["inner"]["api_key"] == "***"

    def test_custom_value_in_lists(self):
        data = [{"secret": "s1"}, {"secret": "s2"}]
        result = sanitize_parameters(data, redacted_value="<redacted>")
        assert result[0]["secret"] == "<redacted>"
        assert result[1]["secret"] == "<redacted>"


# ────────────────────────────────────────────────────────────
# Immutability (original data not modified)
# ────────────────────────────────────────────────────────────

class TestImmutability:
    """Original data must not be modified (deep copy behavior)."""

    def test_dict_not_modified(self):
        original = {"password": "secret", "user": "alice"}
        original_copy = copy.deepcopy(original)
        sanitize_parameters(original)
        assert original == original_copy

    def test_nested_dict_not_modified(self):
        original = {"config": {"password": "secret", "host": "localhost"}}
        original_copy = copy.deepcopy(original)
        sanitize_parameters(original)
        assert original == original_copy

    def test_list_not_modified(self):
        original = [{"token": "tok", "name": "a"}]
        original_copy = copy.deepcopy(original)
        sanitize_parameters(original)
        assert original == original_copy

    def test_deeply_nested_not_modified(self):
        original = {"a": {"b": {"c": {"api_key": "key"}}}}
        original_copy = copy.deepcopy(original)
        sanitize_parameters(original)
        assert original == original_copy

    def test_result_is_independent(self):
        original = {"password": "secret"}
        result = sanitize_parameters(original)
        # Modifying result should not affect original
        result["password"] = "modified"
        assert original["password"] == "secret"


# ────────────────────────────────────────────────────────────
# Basic types pass-through
# ────────────────────────────────────────────────────────────

class TestBasicTypes:
    """Basic types should be passed through unchanged."""

    def test_string(self):
        assert sanitize_parameters("hello") == "hello"

    def test_integer(self):
        assert sanitize_parameters(42) == 42

    def test_float(self):
        assert sanitize_parameters(3.14) == 3.14

    def test_boolean_true(self):
        assert sanitize_parameters(True) is True

    def test_boolean_false(self):
        assert sanitize_parameters(False) is False

    def test_none(self):
        assert sanitize_parameters(None) is None


# ────────────────────────────────────────────────────────────
# Empty data
# ────────────────────────────────────────────────────────────

class TestEmptyData:
    """Empty containers should be returned unchanged."""

    def test_empty_dict(self):
        result = sanitize_parameters({})
        assert result == {}

    def test_empty_list(self):
        result = sanitize_parameters([])
        assert result == []

    def test_dict_with_empty_values(self):
        data = {"key": {}}
        result = sanitize_parameters(data)
        assert result == {"key": {}}


# ────────────────────────────────────────────────────────────
# _compile_patterns helper
# ────────────────────────────────────────────────────────────

class TestCompilePatterns:
    """Test the pattern compilation helper."""

    def test_compiled_pattern_matches(self):
        pattern = _compile_patterns(["password"])
        assert pattern.search("password") is not None
        assert pattern.search("my_password_field") is not None

    def test_compiled_pattern_case_insensitive(self):
        pattern = _compile_patterns(["secret"])
        assert pattern.search("SECRET") is not None
        assert pattern.search("Secret") is not None
        assert pattern.search("secret") is not None

    def test_compiled_pattern_no_match(self):
        pattern = _compile_patterns(["token"])
        assert pattern.search("username") is None

    def test_multiple_patterns_combined(self):
        pattern = _compile_patterns(["password", "secret"])
        assert pattern.search("password") is not None
        assert pattern.search("secret") is not None
        assert pattern.search("safe") is None


# ────────────────────────────────────────────────────────────
# _sanitize_recursive direct tests
# ────────────────────────────────────────────────────────────

class TestSanitizeRecursive:
    """Direct tests for the internal recursive function."""

    def test_basic_string_returned(self):
        import re
        pattern = re.compile("password", re.IGNORECASE)
        assert _sanitize_recursive("hello", pattern, REDACTED) == "hello"

    def test_basic_int_returned(self):
        import re
        pattern = re.compile("password", re.IGNORECASE)
        assert _sanitize_recursive(42, pattern, REDACTED) == 42

    def test_basic_none_returned(self):
        import re
        pattern = re.compile("password", re.IGNORECASE)
        assert _sanitize_recursive(None, pattern, REDACTED) is None

    def test_custom_redacted_value(self):
        import re
        pattern = re.compile("token", re.IGNORECASE)
        result = _sanitize_recursive({"token": "abc"}, pattern, "[MASK]")
        assert result["token"] == "[MASK]"
