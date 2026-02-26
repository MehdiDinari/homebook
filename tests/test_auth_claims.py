import pytest
from fastapi import HTTPException

from app.services.auth import _parse_payload


def test_parse_payload_success() -> None:
    user = _parse_payload(
        {
            "sub": "123",
            "email": "u@example.com",
            "display_name": "User",
            "roles": ["subscriber"],
        }
    )
    assert user.wp_user_id == 123
    assert user.email == "u@example.com"
    assert user.roles == ["subscriber"]


def test_parse_payload_requires_wp_user_id() -> None:
    with pytest.raises(HTTPException):
        _parse_payload({"email": "u@example.com"})
