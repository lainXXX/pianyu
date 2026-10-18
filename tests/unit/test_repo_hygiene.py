"""T0 acceptance 2 & 4: pinned deps and ignored secrets/artifacts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_requirements_are_pinned():
    lines = [
        l.strip()
        for l in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if l.strip() and not l.startswith("#")
    ]
    assert lines, "requirements.txt is empty"
    unpinned = [l for l in lines if "==" not in l]
    assert not unpinned, f"unpinned deps: {unpinned}"


def test_gitignore_covers_artifacts_and_secrets():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for entry in ["models/", "work/", "cookies*.txt", ".venv/"]:
        assert entry in ignored, f"{entry} missing from .gitignore"
