"""Очистка одного тестового оператора и его зависимостей в CSMS.

После прогона POST-тестов сессионная фикстура вызывает cleanup() для
каждого оператора, зарегистрированного в этом прогоне. Она передаёт
точные UUID и имя с префиксом test_.

Модуль проверяет совпадение имени в четырёх схемах CSMS, находит
зависимости по внешним ключам и удаляет строки в одной транзакции.
Как отдельная команда модуль принимает UUID и имя одного оператора.
По умолчанию команда показывает план, для удаления нужен --apply.
Для массовой ручной очистки после сбоя используйте recover_test_operators.
"""

import argparse
import os
from collections import defaultdict
from graphlib import CycleError, TopologicalSorter
from time import monotonic, sleep
from uuid import UUID

import psycopg
from dotenv import load_dotenv
from psycopg import sql

from tests.api.csms.db.fixtures import PROJECT_ROOT, TEST_OPERATOR_PREFIX

SCHEMAS = ("platform", "location", "session", "tariff")
ROOTS = [(schema, "operators") for schema in SCHEMAS]
FOREIGN_KEYS = """
    SELECT pn.nspname, p.relname, cn.nspname, ch.relname,
           array_agg(pa.attname ORDER BY cols.ordinality),
           array_agg(ca.attname ORDER BY cols.ordinality)
    FROM pg_catalog.pg_constraint fk
    JOIN pg_catalog.pg_class p ON p.oid = fk.confrelid
    JOIN pg_catalog.pg_namespace pn ON pn.oid = p.relnamespace
    JOIN pg_catalog.pg_class ch ON ch.oid = fk.conrelid
    JOIN pg_catalog.pg_namespace cn ON cn.oid = ch.relnamespace
    CROSS JOIN LATERAL unnest(fk.confkey, fk.conkey) WITH ORDINALITY
        AS cols(parent_num, child_num, ordinality)
    JOIN pg_catalog.pg_attribute pa ON pa.attrelid = p.oid AND pa.attnum = cols.parent_num
    JOIN pg_catalog.pg_attribute ca ON ca.attrelid = ch.oid AND ca.attnum = cols.child_num
    WHERE fk.contype = 'f'
    GROUP BY fk.oid, pn.nspname, p.relname, cn.nspname, ch.relname
    ORDER BY pn.nspname, p.relname, cn.nspname, ch.relname, fk.oid
"""


def build_plan(foreign_keys):
    """Выстраивает порядок от операторов к зависимым таблицам."""
    children = defaultdict(list)
    for ps, pt, cs, ct, parent_columns, child_columns in foreign_keys:
        children[ps, pt].append(((cs, ct), tuple(zip(parent_columns, child_columns, strict=True))))

    dependencies = defaultdict(list)
    pending = list(ROOTS)
    seen = set(ROOTS)
    for parent in pending:
        for child, columns in children[parent]:
            if child[0] not in SCHEMAS:
                raise ValueError(f"Нужна проверка зависимости вне схем CSMS: {'.'.join(child)}")
            dependencies[child].append((parent, columns))
            if child not in seen:
                pending.append(child)
                seen.add(child)

    graph = TopologicalSorter()
    for table in pending:
        graph.add(table, *(parent for parent, _ in dependencies[table]))
    return [(table, dependencies[table]) for table in graph.static_order()]


def check_write_access(connection):
    if connection.execute("SHOW transaction_read_only").fetchone()[0] != "off":
        raise ValueError("Подключение к PostgreSQL доступно только для чтения.")
    plan = build_plan(connection.execute(FOREIGN_KEYS).fetchall())
    relations = [sql.Identifier(*table).as_string(connection) for table, _ in plan]
    missing = connection.execute(
        """
        SELECT relation FROM unnest(%s::text[]) AS relations(relation)
        WHERE NOT (has_table_privilege(relation, 'SELECT') AND has_table_privilege(relation, 'DELETE'))
        """,
        (relations,),
    ).fetchall()
    if missing:
        raise ValueError(f"Роль PostgreSQL не имеет SELECT и DELETE на {len(missing)} таблицах плана CSMS.")
    if not connection.execute(
        "SELECT has_database_privilege(current_database(), 'TEMP')"
    ).fetchone()[0]:
        raise ValueError("Роль PostgreSQL не может создавать временные таблицы для очистки.")


def cleanup(
    connection, operator_id: UUID, expected_name: str, *,
    apply: bool, wait_for_replicas=False, allow_orphan=False,
):
    if not expected_name.startswith(TEST_OPERATOR_PREFIX):
        raise ValueError("Требуется точное имя оператора с префиксом test_.")

    if wait_for_replicas:
        deadline = monotonic() + 15
        for root in ROOTS[1:]:
            query = sql.SQL("SELECT name FROM {} WHERE id = %s").format(sql.Identifier(*root))
            while connection.execute(query, (operator_id,)).fetchone() is None:
                if monotonic() >= deadline:
                    raise ValueError(f"Копия оператора не появилась в {'.'.join(root)} за 15 секунд.")
                sleep(0.25)

    with connection.transaction():
        connection.execute(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ "
            + ("READ WRITE" if apply else "READ ONLY")
        )
        found = False
        for root in ROOTS:
            row = connection.execute(
                sql.SQL("SELECT name FROM {} WHERE id = %s").format(sql.Identifier(*root)),
                (operator_id,),
            ).fetchone()
            if (root == ROOTS[0] and row is None and not allow_orphan) or (
                row is not None and row[0] != expected_name
            ):
                raise ValueError(f"UUID и имя не совпали в {'.'.join(root)}.")
            found = found or row is not None
        if not found:
            raise ValueError("Оператор с указанным UUID не найден ни в одной схеме CSMS.")

        plan = build_plan(connection.execute(FOREIGN_KEYS).fetchall())

        # Замораживаем набор строк до удаления, чтобы последующие DELETE
        # не потеряли исходные связи. tableoid отличает секции таблиц.
        selections = {}
        ctes = []
        for index, (table, dependencies) in enumerate(plan):
            selection = sql.Identifier(f"target_{index}")
            selections[table] = selection
            predicates = []
            if table in ROOTS:
                predicates.append(sql.SQL("item.id = %(operator_id)s AND item.name = %(name)s"))
            for parent, columns in dependencies:
                joins = sql.SQL(" AND ").join(
                    sql.SQL("parent.{} = item.{}").format(sql.Identifier(p), sql.Identifier(c))
                    for p, c in columns
                )
                predicates.append(sql.SQL("EXISTS (SELECT 1 FROM {} AS parent WHERE {})").format(
                    selections[parent], joins
                ))
            ctes.append(sql.SQL("{} AS (SELECT item.*, item.tableoid AS target_tableoid, "
                                "item.ctid AS target_ctid FROM {} AS item WHERE {})").format(
                selection, sql.Identifier(*table), sql.SQL(" OR ").join(
                    sql.SQL("({})").format(predicate) for predicate in predicates
                )
            ))

        params = {"operator_id": operator_id, "name": expected_name}
        names = list(selections.values())
        snapshot = sql.SQL("WITH {} ").format(sql.SQL(", ").join(ctes))
        conflicts = []
        for table, dependencies in plan:
            for parent, columns in dependencies:
                present = sql.SQL(" AND ").join(
                    sql.SQL("item.{} IS NOT NULL").format(sql.Identifier(child)) for _, child in columns
                )
                matches = sql.SQL(" AND ").join(
                    sql.SQL("owner.{} = item.{}").format(sql.Identifier(key), sql.Identifier(child))
                    for key, child in columns
                )
                conflicts.append(sql.SQL(
                    "SELECT {} WHERE EXISTS (SELECT 1 FROM {} AS item "
                    "WHERE {} AND NOT EXISTS (SELECT 1 FROM {} AS owner WHERE {}))"
                ).format(sql.Literal(".".join(table)), selections[table], present, selections[parent], matches))
        if conflicts:
            conflict = connection.execute(
                snapshot + sql.SQL(" UNION ALL ").join(conflicts) + sql.SQL(" LIMIT 1"), params
            ).fetchone()
            if conflict:
                raise ValueError(f"{conflict[0]}: найдена связь с данными вне набора тестового оператора.")

        if not apply:
            counts = snapshot + sql.SQL(" UNION ALL ").join(
                sql.SQL("SELECT {}, count(*) FROM {}").format(sql.Literal(index), name)
                for index, name in enumerate(names)
            ) + sql.SQL(" ORDER BY 1")
            for (table, _), (_, count) in zip(plan, connection.execute(counts, params), strict=True):
                print(f"{'.'.join(table)}: {count}")
            return

        # В режиме записи фиксируем идентификаторы выбранных строк в
        # отдельной временной таблице для каждой таблицы приложения.
        with psycopg.ClientCursor(connection) as cursor:
            for index, name in enumerate(names):
                cursor.execute(
                    sql.SQL("CREATE TEMP TABLE {} ON COMMIT DROP AS ").format(sql.Identifier(f"cleanup_{index}"))
                    + snapshot + sql.SQL("SELECT target_tableoid, target_ctid FROM {}").format(name),
                    params,
                )
        for index in reversed(range(len(plan))):
            table, _ = plan[index]
            result = connection.execute(sql.SQL(
                "DELETE FROM {} AS item USING pg_temp.{} AS selected "
                "WHERE item.tableoid = selected.target_tableoid AND item.ctid = selected.target_ctid"
            ).format(sql.Identifier(*table), sql.Identifier(f"cleanup_{index}")))
            print(f"{'.'.join(table)}: {result.rowcount}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operator_id", type=UUID)
    parser.add_argument("operator_name")
    parser.add_argument("--apply", action="store_true", help="Выполнить удаление в тестовой БД CSMS")
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
    try:
        with psycopg.connect(**settings, autocommit=True, connect_timeout=5,
                             options="-c statement_timeout=30000") as connection:
            if args.apply:
                check_write_access(connection)
            cleanup(connection, args.operator_id, args.operator_name, apply=args.apply)
    except CycleError:
        parser.exit(1, "Обнаружен цикл внешних ключей. Порядок удаления нужно проверить вручную.\n")
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    except psycopg.Error as error:
        parser.exit(1, f"Операция отменена: {type(error).__name__}. Код PostgreSQL: {error.sqlstate or '—'}.\n")


if __name__ == "__main__":
    main()
