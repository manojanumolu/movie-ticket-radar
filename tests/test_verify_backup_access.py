"""The DR-bucket access verifier rejects every identity outside the allow-list."""
import json

import pytest

from tools import verify_backup_access as v

ADMIN = "user:owner@example.com"
AGENT = "serviceAccount:service-123456789012@gcp-sa-firestore.iam.gserviceaccount.com"
BUCKET = "//storage.googleapis.com/projects/_/buckets/ticketradar03-dr-backups"
PROJECT = "//cloudresourcemanager.googleapis.com/projects/987654321098"
ORG = "//cloudresourcemanager.googleapis.com/organizations/111"


def result(where, role, *names):
    return {"attachedResourceFullName": where, "iamBinding": {"role": role, "members": list(names)},
            "identityList": {"identities": [{"name": n} for n in names]}}


def analysis(*results, explored=True, errors=None, impersonation=None):
    doc = {"mainAnalysis": {"analysisResults": list(results), "fullyExplored": explored}}
    if errors:
        doc["mainAnalysis"]["nonCriticalErrors"] = errors
    if impersonation is not None:
        doc["serviceAccountImpersonationAnalysis"] = impersonation
    return doc


CLEAN = analysis(result(BUCKET, "roles/storage.objectAdmin", ADMIN),
                 result(BUCKET, "roles/storage.admin", AGENT),
                 result(PROJECT, "roles/owner", ADMIN))


def test_clean_isolated_bucket_passes():
    code, lines = v.check_effective(CLEAN, [ADMIN], [AGENT])
    assert code == 0 and lines[-1] == "EFFECTIVE_ACCESS_OK"


@pytest.mark.parametrize("where,role,name", [
    (PROJECT, "roles/storage.objectViewer", "serviceAccount:firebase-adminsdk-x@ticketradar03.iam.gserviceaccount.com"),
    (PROJECT, "roles/editor", "serviceAccount:123-compute@developer.gserviceaccount.com"),
    (ORG, "roles/storage.admin", "user:org-admin@example.com"),
    (BUCKET, "roles/storage.legacyObjectReader", "projectViewer:ticketradar03-dr-backups"),
    (BUCKET, "roles/storage.objectViewer", "allUsers"),
])
def test_any_inherited_or_extra_identity_fails(where, role, name):
    doc = analysis(*CLEAN["mainAnalysis"]["analysisResults"], result(where, role, name))
    code, lines = v.check_effective(doc, [ADMIN], [AGENT])
    assert code == 1
    assert f"UNEXPECTED IDENTITY {name}" in lines


def test_group_members_and_impersonators_are_counted():
    via_group = result(PROJECT, "roles/storage.objectViewer", "user:group-member@example.com")
    impersonation = [{"fullyExplored": True,
                      "analysisResults": [result(PROJECT, "roles/iam.serviceAccountTokenCreator",
                                                 "user:impersonator@example.com")]}]
    doc = analysis(*CLEAN["mainAnalysis"]["analysisResults"], via_group, impersonation=impersonation)
    code, lines = v.check_effective(doc, [ADMIN], [AGENT])
    assert code == 1
    assert "UNEXPECTED IDENTITY user:group-member@example.com" in lines
    assert "UNEXPECTED IDENTITY user:impersonator@example.com" in lines


@pytest.mark.parametrize("doc,needle", [
    (analysis(*CLEAN["mainAnalysis"]["analysisResults"], explored=False), "not fully explored"),
    (analysis(*CLEAN["mainAnalysis"]["analysisResults"], errors=[{"cause": "PERMISSION_DENIED"}]), "PERMISSION_DENIED"),
    (analysis(result(BUCKET, "roles/storage.objectAdmin", ADMIN)), "Firestore service agent does not appear"),
])
def test_incomplete_analysis_or_missing_agent_fails(doc, needle):
    code, lines = v.check_effective(doc, [ADMIN], [AGENT])
    assert code == 1 and any(needle in line for line in lines)


def test_raw_response_is_required():
    with pytest.raises(v.Unusable):
        v.check_effective([{"identity": ADMIN}], [ADMIN], [AGENT])


def test_bucket_policy_must_be_exact():
    good = {"bindings": [{"role": "roles/storage.objectAdmin", "members": [ADMIN]},
                         {"role": "roles/storage.admin", "members": [AGENT]}]}
    assert v.check_bucket(good, [ADMIN], [AGENT]) == (0, ["BUCKET_POLICY_OK"])
    convenience = {"bindings": good["bindings"] + [
        {"role": "roles/storage.legacyBucketOwner", "members": ["projectOwner:ticketradar03-dr-backups"]}]}
    code, lines = v.check_bucket(convenience, [ADMIN], [AGENT])
    assert code == 1 and lines == ["UNEXPECTED roles/storage.legacyBucketOwner projectOwner:ticketradar03-dr-backups"]
    missing = {"bindings": good["bindings"][:1]}
    assert v.check_bucket(missing, [ADMIN], [AGENT])[1] == [f"MISSING roles/storage.admin {AGENT}"]
    conditional = {"bindings": [{**good["bindings"][0], "condition": {"expression": "true"}}]}
    assert v.check_bucket(conditional, [ADMIN], [AGENT])[0] == 1


def test_member_arguments_are_validated():
    with pytest.raises(v.Unusable):
        v.check_bucket({"bindings": []}, [], [AGENT])
    with pytest.raises(v.Unusable):
        v.check_bucket({"bindings": []}, [ADMIN], ["serviceAccount:anything@x.iam.gserviceaccount.com"])
    with pytest.raises(v.Unusable):
        v.check_bucket({"bindings": []}, ["serviceAccount:robot@x.iam.gserviceaccount.com"], [AGENT])


def test_ancestors():
    alone = [{"type": "project", "id": "ticketradar03-dr-backups"}]
    assert v.check_ancestors(alone, [])[0] == 0
    nested = alone + [{"type": "folder", "id": "42"}, {"type": "organization", "id": "111"}]
    code, lines = v.check_ancestors(nested, [])
    assert code == 3 and "folders/42" in lines[-1] and "organizations/111" in lines[-1]
    assert v.check_ancestors(nested, ["folders/42", "organizations/111"])[0] == 0


def test_cli_exit_codes(tmp_path, capsys):
    path = tmp_path / "analysis.json"
    path.write_text(json.dumps(CLEAN))
    assert v.main(["effective", str(path), "--admin", ADMIN, "--agent", AGENT]) == 0
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    assert v.main(["effective", str(bad), "--admin", ADMIN, "--agent", AGENT]) == 2
    assert "UNUSABLE INPUT" in capsys.readouterr().out


def test_only_the_dr_projects_own_google_managed_agents_are_permitted():
    own = "serviceAccount:service-987654321098@gcp-sa-cloudasset.iam.gserviceaccount.com"
    source = "serviceAccount:service-123456789012@gcp-sa-cloudbuild.iam.gserviceaccount.com"
    user_managed = "serviceAccount:robot@ticketradar03-dr-backups.iam.gserviceaccount.com"
    doc = analysis(*CLEAN["mainAnalysis"]["analysisResults"],
                   result(PROJECT, "roles/cloudasset.serviceAgent", own))
    assert v.check_effective(doc, [ADMIN], [AGENT])[0] == 1                       # not allowed by default
    code, lines = v.check_effective(doc, [ADMIN], [AGENT], "987654321098")
    assert code == 0 and any("Google-managed agent of the DR project" in line for line in lines)
    for other in (source, user_managed):
        bad = analysis(*doc["mainAnalysis"]["analysisResults"], result(PROJECT, "roles/editor", other))
        assert v.check_effective(bad, [ADMIN], [AGENT], "987654321098")[0] == 1
    with pytest.raises(v.Unusable):
        v.check_effective(doc, [ADMIN], [AGENT], "ticketradar03-dr")
