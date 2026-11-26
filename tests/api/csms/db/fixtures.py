"""Фикстуры PostgreSQL для тестов CSMS."""

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

TEST_OPERATOR_PREFIX = "test_"
PROJECT_ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture(scope="session")
def csms_db():
    """Подключение к PostgreSQL CSMS по настройкам тестового стенда."""
    load_dotenv(PROJECT_ROOT / ".env")
    settings = {
        "host": os.getenv("ENERGY_CSMS_DB_HOST"),
        "port": os.getenv("ENERGY_CSMS_DB_PORT", "5432"),
        "dbname": os.getenv("ENERGY_CSMS_DB_NAME"),
        "user": os.getenv("ENERGY_CSMS_DB_USER"),
        "password": os.getenv("ENERGY_CSMS_DB_PASSWORD"),
    }
    if not all(settings.values()):
        pytest.skip("Заполните настройки ENERGY_CSMS_DB_* в .env")

    import psycopg

    try:
        connection = psycopg.connect(
            **settings,
            connect_timeout=5,
            options="-c statement_timeout=5000",
            autocommit=True,
        )
    except psycopg.OperationalError as exc:
        error_type = type(exc).__name__
    else:
        with connection:
            yield connection
        return
    pytest.fail(f"Подключение к PostgreSQL CSMS не удалось ({error_type}).", pytrace=False)


@pytest.fixture(scope="session")
def operators_cleanup_db(csms_db):
    """Проверяет права до отправки запросов, способных создать оператора."""
    from tests.api.csms.db.operator_cleanup import check_write_access

    try:
        check_write_access(csms_db)
    except ValueError as error:
        pytest.skip(str(error))
    return csms_db
