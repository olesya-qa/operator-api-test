from typing import get_args

import pytest
from requests import Response
import pytest_check as check

from tests.api.csms.operators.model_operators import (
    OperatorCreateRequest,
    OperatorItem,
    ProblemDetail,
    VatType,
)

pytestmark = [pytest.mark.energy_csms, pytest.mark.api]
REQUIRED_FIELDS_FOR_NEGATIVE_TESTS = tuple(
    field for field in OperatorCreateRequest.model_fields if field != "companyEmail"
)


@pytest.mark.parametrize("vat_type", get_args(VatType))
def test_post_operators_creates_operator(
    energy_csms_client, post_operator, operator_payload: OperatorCreateRequest, vat_type: VatType
):
    """Проверка создания оператора с обязательными полями и допустимым НДС."""
    payload = operator_payload.model_copy(update={"vatType": vat_type})
    response = post_operator(
        json=payload.model_dump(mode="json"),
        expected_name=payload.name,
    )
    assert response.status_code == 200, (
        f"Не удалось создать оператора: HTTP {response.status_code}"
    )

    created = OperatorItem.model_validate(response.json())
    check.is_false(created.deleted, "Созданный оператор должен быть активным")
    check.is_none(created.deletedAt, "У нового оператора не должно быть даты удаления")
    assert created.createdBy is not None, "POST должен вернуть автора создания"
    assert created.updatedBy is not None, "POST должен вернуть автора последнего изменения"
    check.equal(created.createdBy.id, created.updatedBy.id, "При создании авторы должны совпадать")
    assert created.createdAt is not None, "POST должен вернуть дату создания"
    assert created.updatedAt is not None, "POST должен вернуть дату последнего изменения"
    check.is_true(created.updatedAt >= created.createdAt, "Дата изменения не может быть раньше создания")

    get_response = energy_csms_client.get(f"/v1/operators/{created.id}")
    assert get_response.status_code == 200, (
        f"Не удалось получить созданного оператора {created.id}: HTTP {get_response.status_code}"
    )
    saved = OperatorItem.model_validate(get_response.json())
    check.equal(saved.id, created.id, "GET должен вернуть UUID созданного оператора")
    check.is_false(saved.deleted, "Сохранённый оператор должен быть активным")
    check.equal(saved.createdBy, created.createdBy, "Автор создания в GET должен совпадать с POST")
    check.equal(saved.updatedBy, created.updatedBy, "Автор изменения в GET должен совпадать с POST")
    check.equal(saved.createdAt, created.createdAt, "Дата создания в GET должна совпадать с POST")
    check.equal(saved.updatedAt, created.updatedAt, "Дата изменения в GET должна совпадать с POST")
    check.is_none(saved.deletedAt, "У сохранённого оператора не должно быть даты удаления")

    for field in OperatorCreateRequest.model_fields:
        expected = getattr(payload, field)
        check.equal(getattr(created, field), expected, f"Поле {field} в POST")
        check.equal(getattr(saved, field), expected, f"Сохранённое поле {field} в GET")


def assert_bad_request(response: Response):
    assert response.status_code == 400, (
        f"Ожидался HTTP 400, получен HTTP {response.status_code}"
    )
    error = ProblemDetail.model_validate(response.json())
    check.equal(error.status, 400, "Статус ошибки в теле должен совпадать с HTTP")
    check.is_true(bool(error.code), "Ответ должен содержать код ошибки")


@pytest.mark.parametrize("field", REQUIRED_FIELDS_FOR_NEGATIVE_TESTS)
def test_post_operators_rejects_null_required_field(
    post_operator, operator_payload: OperatorCreateRequest, field: str
):
    """Проверка HTTP 400, когда одно обязательное поле передано как null."""
    payload = operator_payload.model_dump(mode="json")
    payload[field] = None

    response = post_operator(json=payload, expected_name=operator_payload.name)

    assert_bad_request(response)


@pytest.mark.parametrize("field", REQUIRED_FIELDS_FOR_NEGATIVE_TESTS)
def test_post_operators_rejects_missing_required_field(
    post_operator, operator_payload: OperatorCreateRequest, field: str
):
    """Проверка HTTP 400, когда одно обязательное поле пропущено."""
    payload = operator_payload.model_dump(mode="json")
    payload.pop(field)

    response = post_operator(json=payload, expected_name=operator_payload.name)

    assert_bad_request(response)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        pytest.param("vatType", "VAT_UNKNOWN", id="unknown_vat_type"),
        pytest.param("name", ["QA Operator"], id="name_is_array"),
    ],
)
def test_post_operators_rejects_invalid_field_value(
    post_operator, operator_payload: OperatorCreateRequest, field, invalid_value
):
    """Проверка HTTP 400 для неизвестного НДС и поля неверного типа."""
    payload = operator_payload.model_dump(mode="json")
    payload[field] = invalid_value

    response = post_operator(json=payload, expected_name=operator_payload.name)

    assert_bad_request(response)
