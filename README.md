# Автотесты API CSMS проекта «Энергия»

Автотесты создания оператора и получения оператора по UUID. Проект написан на Python с использованием pytest, requests и Pydantic.

## Структура проекта

```text
├── tests/
│   ├── __init__.py
│   └── api/
│       ├── __init__.py
│       └── csms/
│           ├── __init__.py
│           ├── db/
│           │   ├── __init__.py
│           │   ├── operator_cleanup.py    # Очистка одного оператора, используется pytest
│           │   ├── recover_test_operators.py  # Ручная массовая очистка после сбоя
│           │   └── fixtures.py             # Фикстуры PostgreSQL
│           └── operators/
│               ├── __init__.py
│               ├── data.py                  # Фикстуры данных оператора
│               ├── model_operators.py       # Модели запроса, ответа и ошибки
│               ├── post_operators_test.py   # POST /v1/operators
│               └── get_operator_by_id_test.py  # GET /v1/operators/{id}
├── ignore/
│   └── .gitkeep
├── .env.example                             # Шаблон локальной конфигурации
├── .gitignore                               # Правила исключения файлов из Git
├── conftest.py                              # Вход в CSMS и подключение фикстур
├── README.md
├── pytest.ini                               # Настройки pytest
└── requirements.txt                         # Зависимости Python
```

## Установка и настройка

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp -n .env.example .env
```

## Запуск тестов

Обязательные условия перед каждым запуском

1. **Подключение к VPN NetBird** для доступа к тестовому стенду и PostgreSQL.
2. **Актуальные данные для подключения к PostgreSQL** в локальном `.env`, включая `ENERGY_CSMS_DB_USER` и `ENERGY_CSMS_DB_PASSWORD`. Для POST-тестов требуется учётная запись с правами на автоматическую очистку.

Команда запуска из корня проекта

```bash
python -m pytest -m energy_csms -rs
```

- `POST /v1/operators` проверяет HTTP 200, UUID, поля запроса и служебные поля `createdBy`, `updatedBy`, `createdAt`, `updatedAt`, `deleted` и `deletedAt` в ответах POST и последующего GET для всех 12 значений `VatType` из OpenAPI. Каждый тест с данными оператора задаёт уникальное имя с префиксом `test_`. В конце прогона сессионная фикстура находит созданных операторов и очищает их связи в четырёх схемах CSMS, в том числе после падения проверок.
- Негативные сценарии POST проверяют HTTP 400 и схему ошибки при пропуске или `null` для пяти обязательных полей, неизвестном значении НДС и массиве вместо строки.
- `GET /v1/operators/{id}` проверяет HTTP 200, поля ответа и совпадение UUID. Фикстура берёт UUID первого действующего оператора из `GET /v1/operators`. Если список пуст, pytest пропускает тест с указанием причины.
