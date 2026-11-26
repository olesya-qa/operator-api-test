import pytest
import pytest_check as check

from tests.api.csms.operators.model_operators import OperatorItem

pytestmark = [pytest.mark.energy_csms, pytest.mark.api]


def test_get_operator_by_id_returns_operator(energy_csms_client, operator_id):
    """Проверка получения действующего оператора по UUID."""
    response = energy_csms_client.get(f"/v1/operators/{operator_id}")
    assert response.status_code == 200, (
        f"Не удалось получить оператора {operator_id}: HTTP {response.status_code}"
    )

    operator = OperatorItem.model_validate(response.json())
    check.equal(operator.id, operator_id, "Ответ должен содержать запрошенный UUID")
    check.is_false(operator.deleted, "Тестовый оператор должен быть активным")
    check.is_none(operator.deletedAt, "У действующего оператора не должно быть даты удаления")
    if operator.createdAt is not None and operator.updatedAt is not None:
        check.is_true(operator.updatedAt >= operator.createdAt, "Дата изменения не может быть раньше создания")
