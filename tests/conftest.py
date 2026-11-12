"""Keep the real claude binary out of tests that predate the claude provider."""

import sys

sys.dont_write_bytecode = True

import shutil

import pytest


@pytest.fixture(autouse=True)
def hide_claude_binary(monkeypatch):
    # source: Door 26, 11-12: tests must not launch the real claude command.
    # Auto checks shutil.which("claude") first. Existing tests still pin
    # Anthropic, Ollama, and none, so the binary stays hidden unless a test
    # replaces which itself.
    real_which = shutil.which

    def which(cmd, *args, **kwargs):
        if cmd == "claude":
            return None
        return real_which(cmd, *args, **kwargs)

    monkeypatch.setattr(shutil, "which", which)
