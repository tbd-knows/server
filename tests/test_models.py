import pytest
from pydantic import ValidationError
from tbd_server.user.models import Invitation, ProfilePatch, Seed, normalize_phone


def test_approved_details_normalized():
    entry = Invitation(name=" Example ", phone="(212) 555-0100", email=" EXAMPLE@example.test ", type="individual")
    assert entry.name == "Example"
    assert entry.email == "example@example.test"
    assert entry.phone == "+12125550100"


@pytest.mark.parametrize("phone", ["+442071234567", "2125550100ext1", "0005550100", "", "+1abc2125550100"])
def test_invalid_phone(phone):
    with pytest.raises(ValueError):
        normalize_phone(phone)


def test_protected_profile_fields_rejected():
    with pytest.raises(ValidationError):
        ProfilePatch(name="Example", revision=0, email="other@example.test")
    with pytest.raises(ValidationError):
        ProfilePatch(name="Example", revision=True)


def test_seed_needs_no_contacts():
    assert Seed(seed_key="example", name="Example Shop").phone is None
