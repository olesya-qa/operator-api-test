import os
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

import pytest
import requests
from dotenv import load_dotenv

from tests.api.csms.db.fixtures import csms_db, operators_cleanup_db
from tests.api.csms.operators.data import created_operators, operator_id, operator_payload, post_operator

PROJECT_ROOT = Path(__file__).resolve().parent
BROWSER_HEADERS = {"Accept": "text/html"}
REDIRECT_STATUSES = {301, 302, 303}
CALLBACK_PATH = "/login/oauth2/code/keycloak"


class KeycloakLoginForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action = ""
        self.hidden_fields: dict[str, str] = {}
        self._in_login_form = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form":
            action = attributes.get("action") or ""
            self._in_login_form = "/login-actions/authenticate" in urlsplit(action).path
            if self._in_login_form:
                self.action = action
        elif tag == "input" and self._in_login_form and attributes.get("type") == "hidden":
            name = attributes.get("name")
            if name:
                self.hidden_fields[name] = attributes.get("value") or ""

    def handle_endtag(self, tag):
        if tag == "form":
            self._in_login_form = False


def _redirect(response: requests.Response, stage: str) -> str:
    location = response.headers.get("Location")
    if response.status_code not in REDIRECT_STATUSES or not location:
        pytest.fail(f"{stage}: ожидался редирект с Location, получен HTTP {response.status_code}")
    return urljoin(response.url, location)


def _require_server_url(url: str, base_url: str, stage: str):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != urlsplit(base_url).netloc.lower()
        or parsed.username is not None
    ):
        pytest.fail(f"{stage}: адрес должен находиться на настроенном HTTPS-хосте CSMS")


def _login(session: requests.Session, base_url: str, username: str, password: str):
    def request(method, url, stage, **kwargs):
        try:
            return session.request(
                method, url, headers=BROWSER_HEADERS, timeout=30, allow_redirects=False, **kwargs
            )
        except requests.RequestException as exc:
            pytest.fail(f"{stage}: соединение с CSMS не удалось ({type(exc).__name__})", pytrace=False)

    start = request("GET", f"{base_url}/oauth2/authorization/keycloak", "Старт входа")
    auth_url = _redirect(start, "Старт входа")
    _require_server_url(auth_url, base_url, "Старт входа")
    auth_path = urlsplit(auth_url).path
    if not (auth_path.startswith("/auth/realms/") and auth_path.endswith("/openid-connect/auth")):
        pytest.fail("Старт входа: ожидался адрес авторизации Keycloak")

    page = request("GET", auth_url, "Страница Keycloak")
    if page.status_code == 200:
        form = KeycloakLoginForm()
        form.feed(page.text)
        if not form.action:
            pytest.fail("Страница Keycloak: форма логина не найдена")
        action_url = urljoin(page.url, form.action)
        _require_server_url(action_url, base_url, "Форма Keycloak")
        if not urlsplit(action_url).path.startswith("/auth/realms/"):
            pytest.fail("Форма Keycloak: неожиданный путь отправки логина")
        fields = {"credentialId": "", **form.hidden_fields}
        fields.update(username=username, password=password)
        result = request("POST", action_url, "Вход в Keycloak", data=fields)
        if result.status_code == 200 and re.search(
            r"""(?:name|id)\s*=\s*["'](?:otp|totp|otpCode)["']|one-time code""",
            result.text,
            re.IGNORECASE,
        ):
            pytest.fail("Keycloak запросил OTP. Для автотестов нужна учётная запись без OTP.")
        if result.status_code == 200:
            pytest.fail("Keycloak не завершил вход. Проверьте данные тестовой учётной записи.")
    else:
        result = page

    callback_url = _redirect(result, "Вход в Keycloak")
    _require_server_url(callback_url, base_url, "OAuth2-коллбэк")
    callback = urlsplit(callback_url)
    query = parse_qs(callback.query)
    if callback.path != CALLBACK_PATH or not query.get("code") or "error" in query:
        pytest.fail("Вход в Keycloak: OAuth2-коллбэк не содержит действительного кода входа")

    result = request("GET", callback_url, "OAuth2-коллбэк")
    target = _redirect(result, "OAuth2-коллбэк")
    if "/oauth2/authorization/" in urlsplit(target).path or not any(
        cookie.name in {"csms-gateway-session", "csms-sid"} for cookie in session.cookies
    ):
        pytest.fail("OAuth2-коллбэк: CSMS не создал сессию")


@pytest.fixture(scope="session")
def energy_csms_client():
    """Выполняет вход в Keycloak один раз за прогон и передаёт API-клиент с сессией CSMS."""
    load_dotenv(PROJECT_ROOT / ".env")
    base_url = os.getenv("ENERGY_CSMS_BASE_URL", "").strip().rstrip("/")
    username = os.getenv("ENERGY_CSMS_USERNAME")
    password = os.getenv("ENERGY_CSMS_PASSWORD")
    if not base_url or not username or not password:
        pytest.skip("Задайте ENERGY_CSMS_BASE_URL, ENERGY_CSMS_USERNAME и ENERGY_CSMS_PASSWORD в .env")

    parsed_url = urlsplit(base_url)
    if (
        parsed_url.scheme != "https"
        or not parsed_url.hostname
        or parsed_url.username is not None
        or parsed_url.path
        or parsed_url.query
        or parsed_url.fragment
    ):
        pytest.fail("ENERGY_CSMS_BASE_URL должен быть HTTPS-адресом стенда без пути")

    with requests.Session() as session:
        _login(session, base_url, username, password)
        session.headers["Accept"] = "application/json"
        yield CsmsClient(base_url, session)


class CsmsClient:
    def __init__(self, base_url: str, session: requests.Session):
        self.base_url = base_url
        self.session = session

    def get(self, path: str, *, params: dict[str, str | int] | None = None) -> requests.Response:
        return self._request("GET", path, params=params)

    def post(self, path: str, *, json: dict | None = None) -> requests.Response:
        return self._request("POST", path, json=json)

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        response = self.session.request(
            method, f"{self.base_url}{path}", timeout=30, allow_redirects=False, **kwargs
        )
        if response.status_code == 401 or response.is_redirect:
            pytest.fail(f"{method} {path}: сессия CSMS не принята, HTTP {response.status_code}")
        return response
