"""PostgreSQL, один процесс FastAPI и Streamlit."""

import hashlib
import io
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import webbrowser
import zipfile
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from init_env import init_env  # noqa: E402

DATA = ROOT / ".data"
LOGS, RUN = DATA / "logs", DATA / "run"
# initdb в Windows не работает с кириллицей в пути: в таком случае файлы PostgreSQL
# хранятся в локальном каталоге пользователя. Используем существующий кластер
# и отдельную базу msa_product.
PG_ROOT = DATA if str(DATA).isascii() else Path(os.getenv("LOCALAPPDATA", "")) / "msa-core-local"
PG_DATA, PG_HOME = PG_ROOT / "pg", PG_ROOT / "pgsql"
# В Windows берём PostgreSQL 16.2 из архива pgserver с проверкой хеша, иначе — из PATH.
PG_WHEEL = (
    "https://files.pythonhosted.org/packages/85/80/"
    "f6304274c1740c283bc7317ababceb3c23c8275ce4995f7379e17b49bc6d/"
    "pgserver-0.1.4-cp312-cp312-win_amd64.whl",
    "406e9355334e40754160a33d93f18a848720a38cd0b68da50be2ea272c89ed2d",
)
DEEPSEEK_MODEL = "deepseek-flash"
LOCAL_URL = "http://127.0.0.1:1234/v1"  # Стандартный сервер LM Studio.
# Эти порты не пересекаются со стандартными портами прежнего стенда.
PORTS = {"postgres": 5433, "api": 8091, "ui": 8501}
API = f"http://127.0.0.1:{PORTS['api']}"
HEALTH = {"api": f"{API}/health", "ui": "http://127.0.0.1:8501/_stcore/health"}


def say(text):
    print(text, flush=True)


def env_file():
    values = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            name, value = line.split("=", 1)
            values[name] = value.strip("'")
    return values


def child_env(extra):
    # Ключи LLM получает только процесс API.
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"DEEPSEEK_API_KEY", "MSA_LLM_API_KEY", "VLLM_API_KEY"}
    }
    return {**env, **extra}


# PostgreSQL -------------------------------------------------------------------------


def pg_bin(name):
    if sys.platform != "win32":
        return name
    path = PG_HOME / "pgserver" / "pginstall" / "bin" / f"{name}.exe"
    if not path.exists():
        say("Скачиваю PostgreSQL 16 (pgserver, ~40 МБ)…")
        url, expected = PG_WHEEL
        with urlopen(url, timeout=300) as response:
            wheel = response.read()
        if hashlib.sha256(wheel).hexdigest() != expected:
            raise SystemExit("PostgreSQL: контрольная сумма архива не совпала")
        with zipfile.ZipFile(io.BytesIO(wheel)) as archive:
            members = [m for m in archive.namelist() if m.startswith("pgserver/pginstall/")]
            archive.extractall(PG_HOME, members)
    return str(path)


def pg_running():
    result = subprocess.run([pg_bin("pg_ctl"), "-D", str(PG_DATA), "status"], capture_output=True)
    return result.returncode == 0


def pg_up(password):
    fresh = not (PG_DATA / "PG_VERSION").exists()
    if fresh:
        say(f"PostgreSQL: первый запуск, создаю кластер в {PG_DATA}")
        RUN.mkdir(parents=True, exist_ok=True)
        pwfile = RUN / f"pw-{secrets.token_hex(4)}"
        pwfile.write_text(password)
        try:
            subprocess.run(
                [
                    pg_bin("initdb"),
                    "-D",
                    str(PG_DATA),
                    "-U",
                    "msa",
                    "-A",
                    "scram-sha-256",
                    f"--pwfile={pwfile}",
                    "-E",
                    "UTF8",
                    "--locale=C",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
        finally:
            pwfile.unlink()
    if not pg_running():
        busy("postgres")
        subprocess.run(
            [
                pg_bin("pg_ctl"),
                "-D",
                str(PG_DATA),
                "-l",
                str(LOGS / "postgres.log"),
                "-o",
                f"-p {PORTS['postgres']} -h 127.0.0.1",
                "-w",
                "start",
            ],
            check=True,
            # Сервер наследует дескрипторы; канал вывода оставался бы открыт всё время его работы.
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    # Повторяем создание базы: первый запуск мог оборваться после инициализации кластера.
    created = subprocess.run(
        [
            pg_bin("createdb"),
            "-h",
            "127.0.0.1",
            "-p",
            str(PORTS["postgres"]),
            "-U",
            "msa",
            "msa_product",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PGPASSWORD": password},
    )
    if created.returncode and "already exists" not in created.stderr:
        raise SystemExit(created.stderr)


# Процессы ---------------------------------------------------------------------------


def busy(name):
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(("127.0.0.1", PORTS[name])) == 0:
            raise SystemExit(f"Порт {PORTS[name]} ({name}) занят другим процессом")


def healthy(name):
    try:
        with urlopen(HEALTH[name], timeout=5) as response:
            return response.status == 200 and (
                name != "api" or json.load(response).get("application") == "macrostress"
            )
    except (URLError, OSError):
        return False


def start(name, command, env):
    if healthy(name):
        say(f"{name}: уже работает")
        return None
    busy(name)
    log = open(LOGS / f"{name}.log", "ab")  # noqa: SIM115 — дескриптор передаётся дочернему процессу
    flags = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
        if sys.platform == "win32"
        else {"start_new_session": True}
    )
    process = subprocess.Popen(
        command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, **flags
    )
    (RUN / f"{name}.pid").write_text(str(process.pid))
    return process


def wait(name, process, seconds):
    if process is None:  # Процесс уже запущен и отвечает.
        return
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if healthy(name):
            say(f"{name}: готов")
            return
        if process.poll() is not None:
            break
        time.sleep(2)
    tail = (LOGS / f"{name}.log").read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
    raise SystemExit(f"{name}: не поднялся, конец журнала:\n" + "\n".join(tail))


def stop(name):
    pidfile = RUN / f"{name}.pid"
    if not pidfile.exists():
        return
    pid = int(pidfile.read_text())
    # Останавливаем дерево процессов: рабочий процесс uvicorn может быть дочерним.
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(pid, signal.SIGTERM)  # При запуске создана отдельная сессия процессов.
        except ProcessLookupError:
            pass
    pidfile.unlink()
    say(f"{name}: остановлен")


# Команды ----------------------------------------------------------------------------


def served_models(base_url, key=""):
    request = Request(base_url.rstrip("/") + "/models", headers={"Authorization": f"Bearer {key}"})
    with urlopen(request, timeout=15) as response:
        return [item["id"] for item in json.load(response)["data"]]


def llm_env(profile):
    """Подготовить окружение API для выбранного профиля LLM до запуска стенда."""
    if profile == "deepseek":
        key = os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise SystemExit("Нет DEEPSEEK_API_KEY в переменных окружения пользователя")
        say(f"LLM: DeepSeek API, {DEEPSEEK_MODEL} (внешняя: только демо-портфели)")
        return {"MSA_LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": key}
    if profile == "local":
        url = os.environ.get("MSA_LLM_BASE_URL", LOCAL_URL)
        try:
            models = served_models(url, os.environ.get("MSA_LLM_API_KEY", ""))
        except (URLError, OSError) as exc:
            raise SystemExit(
                f"Локальная LLM не отвечает на {url}: запустите сервер LM Studio "
                "(Developer → Start Server) и загрузите модель, например gemma-4-12b"
            ) from exc
        model = os.environ.get("MSA_LLM_MODEL") or next(
            (m for m in models if "embed" not in m.lower()), None
        )
        if not model:
            raise SystemExit(f"На {url} не загружена ни одна модель")
        say(f"LLM: локальная, {model} на {url}")
        return {
            "MSA_LLM_PROVIDER": "local",
            "MSA_LLM_BASE_URL": url,
            "MSA_LLM_MODEL": model,
            "MSA_LLM_API_KEY": os.environ.get("MSA_LLM_API_KEY", ""),
        }
    if profile == "mock":
        say("LLM: заглушка — фиксированный сценарий, настоящий расчёт и MCP")
        return {"MSA_LLM_PROVIDER": "mock"}
    raise SystemExit("Профиль LLM: deepseek | local | mock")


def up(profile="mock"):
    from sqlalchemy.engine import make_url

    for path in (LOGS, RUN):
        path.mkdir(parents=True, exist_ok=True)
    init_env(ROOT)
    values = env_file()
    llm = llm_env(profile)
    # При использовании существующего кластера прежние базы остаются без изменений.
    url = make_url(values["MSA_DATABASE_URL"]).set(database="msa_product")
    was_running = PG_DATA.exists() and pg_running()
    pg_up(values["POSTGRES_PASSWORD"])
    # down останавливает только кластер, поднятый этим стендом: уже работающий
    # PostgreSQL может обслуживать другие локальные проекты.
    if not was_running:
        (RUN / "postgres.owned").write_text("started by this checkout")
    api = start(
        "api",
        [
            sys.executable,
            "-m",
            "uvicorn",
            "msa.api:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(PORTS["api"]),
            "--workers",
            "1",
            "--loop",
            "msa.__main__:loop_factory",
        ],
        child_env({"MSA_DATABASE_URL": url.render_as_string(hide_password=False), **llm}),
    )
    wait("api", api, 90)
    ui = start(
        "ui",
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "ui/app.py",
            "--server.address",
            "127.0.0.1",
            "--server.port",
            str(PORTS["ui"]),
        ],
        child_env({"MSA_API_URL": API}),
    )
    wait("ui", ui, 60)
    say(f"Стек готов: http://127.0.0.1:{PORTS['ui']} · API: {API}/docs")
    say("База msa_product · журналы .data/logs · остановка: msa.ps1 down")
    if os.environ.get("MSA_NO_BROWSER") != "1":
        webbrowser.open(f"http://127.0.0.1:{PORTS['ui']}")


def down():
    stop("ui")
    stop("api")
    owned = RUN / "postgres.owned"
    if owned.exists() and PG_DATA.exists() and pg_running():
        subprocess.run(
            [pg_bin("pg_ctl"), "-D", str(PG_DATA), "-m", "fast", "-w", "stop"],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        say(f"postgres: остановлен, данные сохранены в {PG_DATA}")
        owned.unlink()


def status():
    postgres = PG_DATA.exists() and pg_running()
    say(f"postgres   {'работает' if postgres else 'остановлен'}")
    for name in HEALTH:
        say(f"{name:<10} {'работает' if healthy(name) else 'не отвечает'}")


def logs(name=None):
    for path in sorted(LOGS.glob(f"{name or '*'}.log")):
        tail = path.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
        say(f"==> {path.name}\n" + "\n".join(tail))


def main():
    command, *args = sys.argv[1:] or ["status"]
    commands = {
        "up": up,
        "down": down,
        "status": status,
        "logs": logs,
    }
    if command not in commands:
        raise SystemExit("Команды: up [deepseek|local|mock] | down | status | logs [name]")
    commands[command](*args)


if __name__ == "__main__":
    main()
