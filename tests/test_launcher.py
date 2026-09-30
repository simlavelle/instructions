import socket

import run


def test_free_port_skips_a_busy_port():
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        taken = busy.getsockname()[1]
        assert run.free_port("127.0.0.1", taken) > taken


def test_environment_is_rebuilt_when_requirements_change(tmp_path, monkeypatch):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("fastapi\n")
    monkeypatch.setattr(run, "REQUIREMENTS", requirements)
    before = run.requirements_hash()
    requirements.write_text("fastapi\nnumpy\n")
    assert run.requirements_hash() != before
