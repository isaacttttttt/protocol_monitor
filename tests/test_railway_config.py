import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_config(name: str) -> dict:
    with (PROJECT_ROOT / name).open("rb") as config_file:
        return tomllib.load(config_file)


def test_scheduled_service_uses_cron_entrypoint():
    deploy = _load_config("railway.toml")["deploy"]

    assert deploy["startCommand"] == "python -m app.main scheduled-report --hours 1 --send"
    assert deploy["cronSchedule"] == "30 14-20 * * 1-5"
    assert deploy["restartPolicyType"] == "NEVER"


def test_manual_trigger_uses_the_same_cron_service():
    assert not (PROJECT_ROOT / "railway.manual.toml").exists()
