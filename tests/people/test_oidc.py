import pytest
from django.contrib.auth import models as auth_models

from pandora.people import oidc
from pandora.people.models import Membership, Role, Team

pytestmark = pytest.mark.django_db

CONFIGURED = {
    "PANDORA_OIDC_ISSUER": "https://keycloak.test/realms/pandora",
    "PANDORA_OIDC_CLIENT_ID": "pandora",
    "PANDORA_OIDC_CLIENT_SECRET": "shh",
}


@pytest.fixture
def configured(settings):
    for name, value in CONFIGURED.items():
        setattr(settings, name, value)
    return settings


# whether single sign-on is on at all


def test_sso_is_off_until_all_three_settings_are_present(settings):
    """Should stay off by default — most installs are one person on a laptop."""
    settings.PANDORA_OIDC_ISSUER = "https://keycloak.test/realms/pandora"
    settings.PANDORA_OIDC_CLIENT_ID = ""
    settings.PANDORA_OIDC_CLIENT_SECRET = ""

    assert oidc.enabled() is False


def test_sso_is_on_once_issuer_id_and_secret_are_set(configured):
    """Should need nothing else — the rest is discovered from the issuer."""
    assert oidc.enabled() is True


def test_asking_for_a_client_while_off_says_so(settings):
    """Should fail with the reason rather than an attribute error deeper down."""
    settings.PANDORA_OIDC_ISSUER = ""

    with pytest.raises(oidc.OidcError, match="not configured"):
        oidc.client()


def test_the_provider_is_discovered_from_the_issuer(configured):
    """Should not make the operator paste four endpoint URLs."""
    result = oidc.metadata_url()
    expected = "https://keycloak.test/realms/pandora/.well-known/openid-configuration"

    assert result == expected


def test_a_trailing_slash_on_the_issuer_does_not_double_up(configured):
    """Should accept the URL however Keycloak's admin console printed it."""
    configured.PANDORA_OIDC_ISSUER = "https://keycloak.test/realms/pandora/"

    result = oidc.metadata_url()

    assert "//.well-known" not in result


# which name the account gets


def test_the_preferred_username_wins():
    """Should match what the person sees in the provider's own account page."""
    result = oidc.username_from(
        {"preferred_username": "dev", "email": "dev@shop.test", "sub": "uuid"}
    )
    expected = "dev"

    assert result == expected


def test_the_email_is_used_when_there_is_no_username():
    """Should still produce a readable name in the audit log."""
    result = oidc.username_from({"email": "dev@shop.test", "sub": "uuid"})
    expected = "dev@shop.test"

    assert result == expected


def test_the_subject_is_the_last_resort():
    """Should sign someone in even from a token with no profile scope."""
    result = oidc.username_from({"sub": "uuid"})
    expected = "uuid"

    assert result == expected


def test_a_token_with_no_name_at_all_is_refused():
    """Should not create an account nobody can be matched to."""
    with pytest.raises(oidc.OidcError, match="no username"):
        oidc.username_from({})


# provisioning


def test_a_first_sign_in_creates_a_staff_account(configured):
    """Should let a new colleague in without the operator making an account."""
    user = oidc.provision({"preferred_username": "dev", "email": "dev@shop.test"})

    result = (user.username, user.email, user.is_staff)
    expected = ("dev", "dev@shop.test", True)

    assert result == expected


def test_a_provisioned_account_has_no_usable_password(configured):
    """Should keep the provider the only way in for that account."""
    oidc.provision({"preferred_username": "dev"})

    result = auth_models.User.objects.get(username="dev").has_usable_password()

    assert result is False


def test_a_second_sign_in_reuses_the_account(configured):
    """Should not accumulate one account per sign-in."""
    oidc.provision({"preferred_username": "dev"})
    oidc.provision({"preferred_username": "dev"})

    result = auth_models.User.objects.filter(username="dev").count()
    expected = 1

    assert result == expected


def test_a_changed_email_is_written_through(configured):
    """Should follow the provider, which is the system of record."""
    oidc.provision({"preferred_username": "dev", "email": "old@shop.test"})
    user = oidc.provision({"preferred_username": "dev", "email": "new@shop.test"})

    result = user.email
    expected = "new@shop.test"

    assert result == expected


def test_an_existing_local_account_is_promoted_to_staff(configured):
    """Should let an account made before SSO sign in through it."""
    auth_models.User.objects.create_user(
        username="dev", password="local", is_staff=False
    )

    result = oidc.provision({"preferred_username": "dev"}).is_staff

    assert result is True


def test_an_existing_local_password_is_left_alone(configured):
    """Should not lock someone out of the local login they still use."""
    auth_models.User.objects.create_user(username="dev", password="local")

    oidc.provision({"preferred_username": "dev"})

    result = auth_models.User.objects.get(username="dev").check_password("local")

    assert result is True


# group to role mapping


def test_a_group_named_in_the_settings_maps_to_its_role(configured):
    """Should let the provider decide who may triage."""
    configured.PANDORA_OIDC_MEMBER_GROUP = "engineers"

    oidc.provision({"preferred_username": "dev", "groups": ["engineers"]})

    result = Membership.objects.get(user__username="dev").role
    expected = Role.MEMBER

    assert result == expected


def test_the_owner_group_beats_the_member_group(configured):
    """Should not demote an owner who is also in the engineers group."""
    configured.PANDORA_OIDC_OWNER_GROUP = "platform"
    configured.PANDORA_OIDC_MEMBER_GROUP = "engineers"

    oidc.provision({"preferred_username": "dev", "groups": ["engineers", "platform"]})

    result = Membership.objects.get(user__username="dev").role
    expected = Role.OWNER

    assert result == expected


def test_an_unmapped_group_falls_back_to_the_default_role(configured):
    """Should let someone read without the operator mapping every group."""
    configured.PANDORA_OIDC_DEFAULT_ROLE = Role.VIEWER

    oidc.provision({"preferred_username": "dev", "groups": ["marketing"]})

    result = Membership.objects.get(user__username="dev").role
    expected = Role.VIEWER

    assert result == expected


def test_no_default_role_leaves_the_account_out_of_every_team(configured):
    """Should let an operator require the membership to be granted by hand."""
    configured.PANDORA_OIDC_DEFAULT_ROLE = ""

    user = oidc.provision({"preferred_username": "dev"})

    assert Membership.objects.count() == 0
    assert user.is_staff is False


def test_losing_the_last_oidc_role_revokes_staff_access(configured):
    configured.PANDORA_OIDC_OWNER_GROUP = "platform"
    configured.PANDORA_OIDC_DEFAULT_ROLE = ""
    oidc.provision({"preferred_username": "dev", "groups": ["platform"]})

    user = oidc.provision({"preferred_username": "dev", "groups": []})

    assert Membership.objects.filter(user=user).count() == 0
    assert user.is_staff is False


def test_a_comma_separated_groups_claim_is_split(configured):
    """Should accept the string form some providers send instead of a list."""
    configured.PANDORA_OIDC_OWNER_GROUP = "platform"

    oidc.provision({"preferred_username": "dev", "groups": "marketing, platform"})

    result = Membership.objects.get(user__username="dev").role
    expected = Role.OWNER

    assert result == expected


def test_the_groups_claim_name_is_configurable(configured):
    """Should work against a provider that calls it roles or realm_access."""
    configured.PANDORA_OIDC_GROUPS_CLAIM = "roles"
    configured.PANDORA_OIDC_OWNER_GROUP = "platform"

    oidc.provision({"preferred_username": "dev", "roles": ["platform"]})

    result = Membership.objects.get(user__username="dev").role
    expected = Role.OWNER

    assert result == expected


def test_the_sso_team_is_created_once_and_reused(configured):
    """Should not make a team per person signing in."""
    oidc.provision({"preferred_username": "dev"})
    oidc.provision({"preferred_username": "ops"})

    result = Team.objects.count()
    expected = 1

    assert result == expected


def test_a_changed_group_moves_the_role_on_the_next_sign_in(configured):
    """Should follow a promotion or a revocation made in the provider."""
    configured.PANDORA_OIDC_OWNER_GROUP = "platform"
    oidc.provision({"preferred_username": "dev", "groups": ["platform"]})

    oidc.provision({"preferred_username": "dev", "groups": []})

    result = Membership.objects.get(user__username="dev").role
    expected = Role.VIEWER

    assert result == expected


def test_the_client_is_registered_with_the_configured_scopes(configured):
    """Should ask for the scopes the operator's provider is willing to release."""
    configured.PANDORA_OIDC_SCOPES = "openid email groups"

    result = oidc.client().client_kwargs["scope"]
    expected = "openid email groups"

    assert result == expected
