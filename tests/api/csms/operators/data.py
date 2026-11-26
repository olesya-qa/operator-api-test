"""Тестовые данные и фикстуры для операторов CSMS."""

import os
from uuid import UUID, uuid4

import pytest
from psycopg import Error
from pydantic import TypeAdapter

from tests.api.csms.db.operator_cleanup import cleanup
from tests.api.csms.db.fixtures import TEST_OPERATOR_PREFIX
from tests.api.csms.operators.model_operators import OperatorCreateRequest


@pytest.fixture(scope="session")
def created_operators(operators_cleanup_db):
    """Очищает операторов текущего прогона после завершения тестов."""
    operators: dict[str, UUID | None] = {}
    yield operators

    failures = []
    for name, expected_id in operators.items():
        try:
            rows = operators_cleanup_db.execute(
                "SELECT id FROM platform.operators WHERE name = %s", (name,)
            ).fetchall()
            if len(rows) > 1:
                raise ValueError("найдено несколько операторов с одинаковым именем")
            if expected_id is not None and (not rows or rows[0][0] != expected_id):
                raise ValueError(f"UUID {expected_id} из POST не найден под этим именем в подключённой БД")
            if rows:
                operator_uuid = rows[0][0]
                cleanup(operators_cleanup_db, operator_uuid, name, apply=True, wait_for_replicas=True)
        except (ValueError, Error) as error:
            detail = str(error) if isinstance(error, ValueError) else f"PostgreSQL {error.sqlstate or type(error).__name__}"
            failures.append(f"{name}: {detail}")
    if failures:
        pytest.fail("Не удалось очистить операторов:\n" + "\n".join(failures), pytrace=False)


@pytest.fixture
def operator_payload(created_operators):
    """Формирует данные оператора и запоминает его имя до отправки POST."""
    yoomoney_merchant = os.getenv("ENERGY_CSMS_TEST_YOOMONEY_MERCHANT")
    sberbank_merchant = os.getenv("ENERGY_CSMS_TEST_SBERBANK_MERCHANT_LOGIN")
    company_inn = os.getenv("ENERGY_CSMS_TEST_COMPANY_INN")
    if not all((yoomoney_merchant, sberbank_merchant, company_inn)):
        pytest.skip("Заполните параметры ENERGY_CSMS_TEST_* в .env для POST-тестов")

    suffix = uuid4().hex
    name = f"{TEST_OPERATOR_PREFIX}Operator {suffix}"
    created_operators[name] = None
    return OperatorCreateRequest(
        name=name,
        yoomoneyMerchant=yoomoney_merchant,
        sberbankMerchantLogin=sberbank_merchant,
        vatType="VAT_NONE",
        companyInn=company_inn,
        companyEmail=f"qa-{suffix}@example.com",
    )


@pytest.fixture
def post_operator(energy_csms_client, created_operators):
    """Регистрирует UUID успешного POST до проверки тела ответа."""
    def send(*, json, expected_name=None):
        response = energy_csms_client.post("/v1/operators", json=json)
        if not 200 <= response.status_code < 300:
            return response

        try:
            body = response.json()
            operator_id = UUID(str(body["id"]))
        except (KeyError, TypeError, ValueError):
            pytest.fail(
                "POST вернул успешный статус без читаемого UUID. "
                "Проверьте, была ли создана запись в БД.",
                pytrace=False,
            )
        if expected_name not in created_operators or body.get("name") != expected_name:
            pytest.fail(
                f"POST неожиданно создал оператора с UUID {operator_id} без подтверждённого "
                "уникального имени этого теста. Требуется проверка записи для безопасной очистки.",
                pytrace=False,
            )
        created_operators[expected_name] = operator_id
        return response

    return send


@pytest.fixture
def operator_id(energy_csms_client) -> UUID:
    """Возвращает UUID первого действующего оператора из списка."""
    response = energy_csms_client.get("/v1/operators", params={"deleted": "EXCLUDE", "size": 1})
    assert response.status_code == 200, (
        f"Не удалось получить список операторов: HTTP {response.status_code}"
    )

    items = response.json()["items"]
    if not items:
        pytest.skip("В CSMS нет действующих операторов для проверки GET по UUID")

    return TypeAdapter(UUID).validate_python(items[0]["id"])
