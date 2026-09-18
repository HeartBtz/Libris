import re
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[2] / ".gitlab-ci.yml"


def job_block(name: str) -> str:
    match = re.search(rf"^{re.escape(name)}:\n((?:[ \t].*\n|\n)+)", PIPELINE.read_text(), re.M)
    assert match, f"job {name} not found"
    return match.group(1)


def test_production_deployment_waits_for_every_image_gate():
    # `needs` ignores stage ordering, so a gate missing here runs in parallel with the deployment.
    block = job_block("deploy-production")
    needs = re.search(r"^  needs:\n((?:    .*\n)+)", block, re.M)
    assert needs, "deploy-production must declare its gates explicitly"
    jobs = set(re.findall(r"- job: ([\w-]+)", needs.group(1)))
    assert {"release-images", "container-runtime", "container-scan", "publish-gitlab"} <= jobs


def test_image_gates_run_on_release_tags():
    for name in ("container-runtime", "container-scan"):
        assert "allow_failure: true" not in job_block(name)


def test_release_tags_promote_only_images_verified_on_the_default_branch():
    # No `needs` on the marker job: it runs after every test and image check of the branch pipeline.
    marker = job_block("verified-image")
    assert "needs:" not in marker and "verified-sha-$CI_COMMIT_SHA" in marker
    assert "verified-sha-$CI_COMMIT_SHA" in job_block("release-images")


def test_rollback_is_manual_and_serialized_with_the_deployment():
    block = job_block("rollback-production")
    assert "when: manual" in block and "--rollback" in block
    lock = re.search(r"^  resource_group: (\S+)$", job_block("deploy-production"), re.M).group(1)
    assert f"resource_group: {lock}" in block


def test_every_ci_container_is_named_labelled_and_removable():
    # An anonymous container outlives a cancelled job: the after_script can only remove what it can find.
    runs = re.findall(r"docker run (.*?)(?:\"\$[A-Z_]+IMAGE[A-Z_]*\"|\$\{?[A-Z_]+IMAGE)", PIPELINE.read_text(), re.S)
    assert runs
    for options in runs:
        assert '--label "libris-ci-job=$CI_JOB_ID"' in options, options
        assert '--name "libris-ci-' in options or '--name "$DB_CONTAINER"' in options, options
    assert 'docker ps --all --quiet --filter "label=libris-ci-job=$CI_JOB_ID"' in job_block(".ci-containers")


def test_every_job_has_its_own_timeout():
    text = PIPELINE.read_text()
    jobs = [name for name in re.findall(r"^([a-z][\w-]*):\n", text, re.M) if name not in {"workflow", "default", "variables"}]
    for name in jobs:
        assert re.search(r"^  timeout: ", job_block(name), re.M) or "extends: .publish-image" in job_block(name), name
