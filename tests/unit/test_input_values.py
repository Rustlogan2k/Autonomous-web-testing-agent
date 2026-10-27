import pytest

from web_testing_agent.envs.input_values import field_type, generate_input_value
from web_testing_agent.envs.types import InputValueCategory

_KNOWN_TYPES = ["text", "email", "number", "tel", "url", "password", "date", "search", "textarea"]


@pytest.mark.parametrize("html_type", [*_KNOWN_TYPES, "color", "range", ""])
def test_generates_a_string_for_every_category(html_type):
    for category in InputValueCategory:
        value = generate_input_value(html_type, category)
        assert isinstance(value, str)


@pytest.mark.parametrize("html_type", _KNOWN_TYPES)
def test_empty_string_category_is_always_empty(html_type):
    assert generate_input_value(html_type, InputValueCategory.EMPTY_STRING) == ""


def test_unknown_type_falls_back_to_text():
    for category in InputValueCategory:
        assert generate_input_value("color", category) == generate_input_value("text", category)


def test_type_mismatch_email_is_not_a_valid_email_shape():
    value = generate_input_value("email", InputValueCategory.TYPE_MISMATCH)
    assert "@" not in value


def test_type_mismatch_number_is_not_numeric():
    value = generate_input_value("number", InputValueCategory.TYPE_MISMATCH)
    assert not value.isdigit()


def test_a_plain_text_field_gets_the_values_its_name_or_label_calls_for():
    assert field_type("text", "telephone") == "tel"
    assert field_type("text", "mobileNumber Mobile number") == "tel"
    assert field_type("text", "userEmail") == "email"
    assert field_type("text", "postalCode") == "postcode"
    assert field_type("text", "birthDate") == "date"
    assert field_type("text", "firstName First name") == "text"
    assert field_type("text", "hotel") == "text"                # not a phone field
    assert field_type("password", "telephone") == "password"    # an explicit type wins
    assert generate_input_value("tel", InputValueCategory.VALID_TYPICAL).isdigit()


def test_boundary_max_is_longer_than_boundary_min():
    # "date" boundaries are calendar extremes (0001-01-01 / 9999-12-31), not a length
    # progression like the other types, so it's excluded here.
    for html_type in (t for t in _KNOWN_TYPES if t != "date"):
        min_len = len(generate_input_value(html_type, InputValueCategory.BOUNDARY_MIN))
        max_len = len(generate_input_value(html_type, InputValueCategory.BOUNDARY_MAX))
        assert max_len > min_len
