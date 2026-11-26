"""Ручная массовая очистка тестовых операторов после сбоя.

Запускайте после завершения всех прогонов на тестовом стенде, если
автоочистка не сработала или нужно убрать старые тестовые записи.
Команда сама находит UUID и имена с префиксом test_ в четырёх схемах
CSMS. В список также попадут операторы с этим префиксом, созданные
вручную, и копии, у которых уже нет основной записи.

Первый запуск показывает план. После проверки списка повторите
команду с --apply. Для каждого оператора используется operator_cleanup.
Если один оператор не удалился, его транзакция откатывается, а
обработка остальных продолжается. Pytest эту команду не вызывает.
"""

import argparse
import os
from collections import defaultdict

import psycopg
from dotenv import load_dotenv
from psycopg import sql

from tests.api.csms.db.operator_cleanup import ROOTS, check_write_access, cleanup
from tests.api.csms.db.fixtures import PROJECT_ROOT, TEST_OPERATOR_PREFIX


def cleanup_test_operators(connection, *, apply: bool) -> tuple[int, int]:
    if apply:
        check_write_access(connection)

    query = sql.SQL(" UNION ALL ").join(
        sql.SQL("SELECT id, name FROM {} WHERE starts_with(name, %(prefix)s)").format(
            sql.Identifier(*root)
        )
        for root in ROOTS
    ) + sql.SQL(" ORDER BY id, name")
    operators = defaultdict(set)
    for operator_id, name in connection.execute(query, {"prefix": TEST_OPERATOR_PREFIX}):
        operators[operator_id].add(name)

    print(f"Операторов с префиксом {TEST_OPERATOR_PREFIX}: {len(operators)}")
    completed = 0
    failures = 0
    for operator_id, names in operators.items():
        if len(names) != 1:
            print(f"{operator_id}: в схемах найдены разные имена, очистка пропущена")
            failures += 1
            continue

        name = next(iter(names))
        print(f"\n{name} ({operator_id})")
        try:
            cleanup(connection, operator_id, name, apply=apply, allow_orphan=True)
        except ValueError as error:
            print(f"Ошибка: {error}")
            failures += 1
        except psycopg.Error as error:
            print(f"Ошибка PostgreSQL: {error.sqlstate or type(error).__name__}")
            failures += 1
        else:
            completed += 1
    return completed, failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Удалить найденные записи в тестовой базе")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    settings = {
        "host": os.getenv("ENERGY_CSMS_DB_HOST"),
        "port": os.getenv("ENERGY_CSMS_DB_PORT", "5432"),
        "dbname": os.getenv("ENERGY_CSMS_DB_NAME"),
        "user": os.getenv("ENERGY_CSMS_DB_USER"),
        "password": os.getenv("ENERGY_CSMS_DB_PASSWORD"),
    }
    if not all(settings.values()):
        parser.error("Заполните ENERGY_CSMS_DB_* в .env.")

    options = "-c statement_timeout=30000"
    if not args.apply:
        options += " -c default_transaction_read_only=on"
    try:
        with psycopg.connect(**settings, autocommit=True, connect_timeout=5, options=options) as connection:
            completed, failures = cleanup_test_operators(connection, apply=args.apply)
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    except psycopg.Error as error:
        parser.exit(1, f"Операция отменена: {error.sqlstate or type(error).__name__}.\n")

    result = "Удалено операторов" if args.apply else "Проверено операторов"
    print(f"\n{result}: {completed}. Ошибок: {failures}.")
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(main())
