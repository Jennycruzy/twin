from twin.gate.__main__ import run


def test_repository_gate_passes_without_restarting_external_services():
    checks = run(skip_tests=True)

    assert {check.name for check in checks} == {"repository", "determinism", "commerce", "operations"}
