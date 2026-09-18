"""Sign-up refuses disposable / temporary email domains.

The refusal lives in ``FirebaseAuth.sign_up`` — the one server-side path every
sign-up takes — and happens before Firebase is asked for anything, so a
refused sign-up leaves no account, no pending session and no document. The
form repeats the check only so the message arrives without a round trip.
"""

from __future__ import annotations

import pytest

from auth import disposable, firebase
from auth.firebase import AuthError
from tests.test_auth import body, fake, on_login_page, run, visitor  # noqa: F401 - fixtures


# ──────────────────────────────────────────────────────────────────────────
# The classifier
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("email", [
    "someone@gmail.com", "Someone@Outlook.com", "a.b@yahoo.co.in", "me@icloud.com",
    "student@iith.ac.in", "dev@company.example", "person@my-own-domain.org",
    "x@unknown-but-perfectly-fine.io",
])
def test_ordinary_and_unfamiliar_domains_are_accepted(email):
    assert disposable.is_disposable(email) is False


@pytest.mark.parametrize("email", [
    "x@mailinator.com", "x@10minutemail.com", "x@guerrillamail.com", "x@yopmail.com",
    "x@temp-mail.org", "x@sharklasers.com", "x@tempmail.dev",
])
def test_listed_disposable_domains_are_refused(email):
    assert disposable.is_disposable(email) is True


def test_case_whitespace_and_subdomains_are_handled():
    assert disposable.is_disposable("  X@MAILINATOR.COM ") is True
    assert disposable.is_disposable("x@inbox.Mailinator.com") is True     # a subdomain of a listed one
    assert disposable.normalise_email("  Ravi.T@Example.COM ") == "Ravi.T@example.com"
    assert disposable.domain_of("a@B.example") == "b.example"


@pytest.mark.parametrize("bad", ["", "   ", "no-at-sign", "@mailinator.com", "x@", "x@y", "x y@z.com", "a@b@c.com"])
def test_malformed_addresses_are_not_disposable_they_are_malformed(bad):
    assert disposable.normalise_email(bad) == ""
    assert disposable.is_disposable(bad) is False


def test_the_denylist_is_auditable_and_sane():
    listed = disposable.denylist()
    assert len(listed) > 300
    assert all(d == d.lower() and " " not in d and "@" not in d and "." in d for d in listed)
    # No ordinary mailbox provider may ever be on it.
    for keeper in ("gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
                   "yahoo.com", "yahoo.co.in", "icloud.com", "me.com", "protonmail.com",
                   "proton.me", "rediffmail.com", "zoho.com", "fastmail.com", "yeah.net", "163.com"):
        assert keeper not in listed, keeper


# ──────────────────────────────────────────────────────────────────────────
# The server-side path
# ──────────────────────────────────────────────────────────────────────────
def test_sign_up_refuses_a_disposable_domain_before_calling_firebase(fake):
    client = firebase.FirebaseAuth()
    with pytest.raises(AuthError) as exc:
        client.sign_up("Temp Person", " Temp@Mailinator.COM ", "Interval99")
    assert exc.value.code == "DISPOSABLE_EMAIL"
    assert str(exc.value) == ("Please use a permanent email address. Disposable or temporary "
                              "email addresses are not supported.")
    assert fake.calls == [] and fake.accounts.get("temp@mailinator.com") is None   # nothing created


def test_sign_up_refuses_a_malformed_address_before_calling_firebase(fake):
    client = firebase.FirebaseAuth()
    with pytest.raises(AuthError) as exc:
        client.sign_up("Nobody", "not-an-address", "Interval99")
    assert exc.value.code == "INVALID_EMAIL" and fake.calls == []


def test_sign_up_normalises_the_domain_and_accepts_a_permanent_address(fake):
    client = firebase.FirebaseAuth()
    creds = client.sign_up("Arjun Rao", "  Arjun@Example.COM ", "Interval99")
    assert creds.email_verified is False
    assert "Arjun@example.com" in fake.accounts          # domain lower-cased, local part kept
    assert [a for a, _ in fake.calls] == ["signUp", "update"]


# ──────────────────────────────────────────────────────────────────────────
# The form
# ──────────────────────────────────────────────────────────────────────────
def test_the_signup_form_says_so_and_leaves_nothing_behind(visitor, fake):
    app = run(auth_mode="signup")
    app.text_input(key="auth_su_name").set_value("Temp Person")
    app.text_input(key="auth_su_email").set_value("temp@10minutemail.com")
    app.text_input(key="auth_su_password").set_value("Interval99")
    app.text_input(key="auth_su_confirm").set_value("Interval99")
    app.button(key="auth_signup").click().run()
    assert not app.exception
    text = body(app)
    assert "Please use a permanent email address" in text
    assert "10minutemail" not in text.replace("temp@10minutemail.com", "")   # no rule named to the person
    assert on_login_page(app)
    assert fake.calls == [] and fake.accounts.get("temp@10minutemail.com") is None
    assert "auth_pending" not in app.session_state and "auth_user" not in app.session_state


def test_the_server_path_refuses_even_if_the_form_did_not(visitor, fake, monkeypatch):
    """The form's own check is a courtesy. Silence it, and the store-side
    refusal still leaves no account."""
    from types import SimpleNamespace

    from ui import login

    # Only the form's reference is replaced; ``auth.disposable`` itself, which
    # ``FirebaseAuth.sign_up`` imports, is untouched.
    monkeypatch.setattr(login, "disposable", SimpleNamespace(is_disposable=lambda email: False))
    app = run(auth_mode="signup")
    app.text_input(key="auth_su_name").set_value("Temp Person")
    app.text_input(key="auth_su_email").set_value("temp@yopmail.com")
    app.text_input(key="auth_su_password").set_value("Interval99")
    app.text_input(key="auth_su_confirm").set_value("Interval99")
    app.button(key="auth_signup").click().run()
    assert "Please use a permanent email address" in body(app)
    assert fake.accounts.get("temp@yopmail.com") is None and "auth_pending" not in app.session_state


def test_google_sign_in_is_untouched(fake):
    """The denylist is a sign-up rule for password accounts; a Google account
    arrives already verified by Google and goes through a different call."""
    fake.add_google_identity("google-tok", email="ravi.g@gmail.com", name="Ravi G")
    creds = firebase.FirebaseAuth().sign_in_with_google("google-tok", "https://app.example/")
    assert creds.email == "ravi.g@gmail.com"
