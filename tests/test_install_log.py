"""Tests for install log."""

from engines.install_log import LOG_PATH, install_log, read_tail


def test_install_log_writes(tmp_path, monkeypatch):
    log_path = tmp_path / "logs" / "install.log"
    monkeypatch.setattr("engines.install_log.APP_DIR", tmp_path)
    monkeypatch.setattr("engines.install_log.LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr("engines.install_log.LOG_PATH", log_path)
    install_log("hello", component="test")
    assert log_path.is_file()
    tail = read_tail(5)
    assert any("hello" in line for line in tail)
