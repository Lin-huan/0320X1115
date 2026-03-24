#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import http.cookiejar
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
DEFAULT_BASE_URL = "https://x1115-k3s-11.k3s-dev.myones.net"
DEFAULT_ORG_UUID = "M81VR3T9"
DEFAULT_TEAM_UUID = "CexqToCd"
DEFAULT_REGION_UUID = "default"
DEFAULT_MEMBER_PASSWORD = "Aa123456789"
DEFAULT_EMAIL_PREFIX = "ones-batch"
DEFAULT_EMAIL_DOMAIN = "example.com"
DEFAULT_LANGUAGE = "zh"
DEFAULT_PROJECT_APP_PATH = "/project"
DEFAULT_PROJECT_API_PREFIX = "/project/api/project"


class ApiError(RuntimeError):
    pass


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


@dataclass
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: str

    def json(self) -> Any:
        return json.loads(self.body) if self.body else {}


@dataclass
class InvitationRecord:
    email: str
    invite_code: str
    invite_link: str
    source: str
    activated: bool = False
    activation_status: str = ""
    activation_message: str = ""


@dataclass
class LoginContext:
    access_token: str
    org_uuid: str
    region_uuid: str
    org_user_uuid: str


def env_default(name: str, fallback: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return fallback
    return value


def load_project_env(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]

        os.environ.setdefault(key, value)


def base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def build_pkce_pair() -> tuple[str, str]:
    verifier = base64url(os.urandom(32))
    challenge = base64url(hashlib.sha256(verifier.encode("utf-8")).digest())
    return verifier, challenge


def truncate(value: str, limit: int = 300) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def serialize_message(payload: Any) -> str:
    if isinstance(payload, str):
        return truncate(payload)
    try:
        return truncate(json.dumps(payload, ensure_ascii=False))
    except TypeError:
        return truncate(str(payload))


def normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def infer_dc_base_url(base_url: str) -> str:
    parsed = urllib.parse.urlparse(normalize_base_url(base_url))
    hostname = parsed.hostname or ""
    if not hostname or hostname.startswith("dc."):
        return normalize_base_url(base_url)

    dc_hostname = f"dc.{hostname}"
    netloc = dc_hostname
    if parsed.port:
        netloc = f"{dc_hostname}:{parsed.port}"
    return urllib.parse.urlunparse((parsed.scheme, netloc, "", "", "", "")).rstrip("/")


def unique_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for item in urls:
        normalized = normalize_base_url(item)
        if normalized in seen:
            continue
        seen.add(normalized)
        results.append(normalized)
    return results


def normalize_api_prefix(prefix: str) -> str:
    prefix = prefix.strip()
    if not prefix:
        return ""
    if not prefix.startswith("/"):
        prefix = "/" + prefix
    return prefix.rstrip("/")


def join_url_path(prefix: str, suffix: str) -> str:
    normalized_prefix = normalize_api_prefix(prefix)
    normalized_suffix = suffix if suffix.startswith("/") else f"/{suffix}"
    return f"{normalized_prefix}{normalized_suffix}"


def generate_random_emails(count: int, prefix: str, domain: str) -> list[str]:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    token = base64url(os.urandom(6)).lower()
    return [f"{prefix}-{stamp}-{token}-{index + 1}@{domain}" for index in range(count)]


def ensure_openssl() -> str:
    openssl_path = shutil.which("openssl")
    if not openssl_path:
        raise ApiError("未找到 openssl，脚本依赖系统 openssl 做 RSA 加密。")
    return openssl_path


def rsa_encrypt(public_key: str, plaintext: str) -> str:
    openssl_path = ensure_openssl()
    with tempfile.TemporaryDirectory() as temp_dir:
        public_key_path = Path(temp_dir) / "public.pem"
        input_path = Path(temp_dir) / "password.txt"
        output_path = Path(temp_dir) / "cipher.bin"

        public_key_path.write_text(public_key, encoding="utf-8")
        input_path.write_bytes(plaintext.encode("utf-8"))

        process = subprocess.run(
            [
                openssl_path,
                "pkeyutl",
                "-encrypt",
                "-pubin",
                "-inkey",
                str(public_key_path),
                "-in",
                str(input_path),
                "-out",
                str(output_path),
                "-pkeyopt",
                "rsa_padding_mode:pkcs1",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        if process.returncode != 0:
            raise ApiError(f"openssl 加密失败: {truncate(process.stderr.strip() or process.stdout.strip())}")

        return base64.b64encode(output_path.read_bytes()).decode("ascii")


def csv_headers() -> list[str]:
    return [
        "email",
        "invite_code",
        "invite_link",
        "source",
        "activated",
        "activation_status",
        "activation_message",
    ]


def load_records_from_file(path: Path) -> list[InvitationRecord]:
    if not path.exists():
        raise ApiError(f"文件不存在: {path}")

    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            records: list[InvitationRecord] = []
            for row in reader:
                email = (row.get("email") or "").strip()
                invite_code = (row.get("invite_code") or row.get("code") or "").strip()
                invite_link = (row.get("invite_link") or "").strip()
                if not email or not invite_code:
                    continue
                records.append(
                    InvitationRecord(
                        email=email,
                        invite_code=invite_code,
                        invite_link=invite_link,
                        source="file",
                    )
                )
            return records

    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else data.get("items", [])
        records = []
        for item in items:
            if not isinstance(item, dict):
                continue
            email = str(item.get("email", "")).strip()
            invite_code = str(item.get("invite_code", item.get("code", ""))).strip()
            invite_link = str(item.get("invite_link", "")).strip()
            if not email or not invite_code:
                continue
            records.append(
                InvitationRecord(
                    email=email,
                    invite_code=invite_code,
                    invite_link=invite_link,
                    source="file",
                )
            )
        return records

    raise ApiError("仅支持从 CSV 或 JSON 文件读取邀请码。")


def save_records(path: Path, records: list[InvitationRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_headers())
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "email": record.email,
                    "invite_code": record.invite_code,
                    "invite_link": record.invite_link,
                    "source": record.source,
                    "activated": "yes" if record.activated else "no",
                    "activation_status": record.activation_status,
                    "activation_message": record.activation_message,
                }
            )


class HttpClient:
    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url = normalize_base_url(base_url)
        self.timeout = timeout
        self.cookies = http.cookiejar.CookieJar()
        self.default_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
        self.no_redirect_opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies),
            NoRedirectHandler(),
        )
        self._set_cookie("timezone", "Asia/Shanghai")
        self._set_cookie("ones-lang", DEFAULT_LANGUAGE)
        self._set_cookie("ones-tz", "Asia%2FShanghai")

    def _set_cookie(self, name: str, value: str) -> None:
        parsed = urllib.parse.urlparse(self.base_url)
        self.cookies.set_cookie(
            http.cookiejar.Cookie(
                version=0,
                name=name,
                value=value,
                port=None,
                port_specified=False,
                domain=parsed.hostname or "",
                domain_specified=True,
                domain_initial_dot=False,
                path="/",
                path_specified=True,
                secure=parsed.scheme == "https",
                expires=None,
                discard=True,
                comment=None,
                comment_url=None,
                rest={},
                rfc2109=False,
            )
        )

    def request(
        self,
        method: str,
        url_or_path: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        expected_status: set[int] | None = None,
        follow_redirects: bool = True,
    ) -> HttpResponse:
        expected = expected_status or {200}
        url = url_or_path if url_or_path.startswith("http") else f"{self.base_url}{url_or_path}"
        request = urllib.request.Request(url=url, data=body, method=method.upper())
        request.add_header("User-Agent", USER_AGENT)
        for key, value in (headers or {}).items():
            request.add_header(key, value)

        opener = self.default_opener if follow_redirects else self.no_redirect_opener
        try:
            response = opener.open(request, timeout=self.timeout)
            status = response.getcode()
            raw_body = response.read()
            response_headers = dict(response.headers.items())
        except urllib.error.HTTPError as error:
            status = error.code
            raw_body = error.read()
            response_headers = dict(error.headers.items())
            if status not in expected:
                body_text = raw_body.decode("utf-8", errors="replace")
                raise ApiError(f"{method.upper()} {url} 失败，HTTP {status}: {truncate(body_text)}") from error
        except urllib.error.URLError as error:
            raise ApiError(f"{method.upper()} {url} 请求失败: {error.reason}") from error

        body_text = raw_body.decode("utf-8", errors="replace")
        if status not in expected:
            raise ApiError(f"{method.upper()} {url} 返回异常状态码 {status}: {truncate(body_text)}")
        return HttpResponse(status=status, headers=response_headers, body=body_text)

    def request_json(
        self,
        method: str,
        url_or_path: str,
        *,
        headers: dict[str, str] | None = None,
        payload: Any | None = None,
        expected_status: set[int] | None = None,
        follow_redirects: bool = True,
    ) -> Any:
        merged_headers = {"Accept": "application/json, text/plain, */*"}
        merged_headers.update(headers or {})
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            merged_headers.setdefault("Content-Type", "application/json;charset=UTF-8")
        response = self.request(
            method,
            url_or_path,
            headers=merged_headers,
            body=body,
            expected_status=expected_status,
            follow_redirects=follow_redirects,
        )
        try:
            return response.json()
        except json.JSONDecodeError as error:
            raise ApiError(f"{method.upper()} {url_or_path} 返回了非 JSON 内容: {truncate(response.body)}") from error

    def request_form(
        self,
        method: str,
        url_or_path: str,
        *,
        headers: dict[str, str] | None = None,
        form_data: dict[str, str],
        expected_status: set[int] | None = None,
        follow_redirects: bool = True,
    ) -> HttpResponse:
        merged_headers = {"Content-Type": "application/x-www-form-urlencoded"}
        merged_headers.update(headers or {})
        return self.request(
            method,
            url_or_path,
            headers=merged_headers,
            body=urllib.parse.urlencode(form_data).encode("utf-8"),
            expected_status=expected_status,
            follow_redirects=follow_redirects,
        )


class OnesInviteClient:
    def __init__(
        self,
        base_url: str,
        org_uuid: str,
        team_uuid: str,
        region_uuid: str,
        *,
        identity_base_url: str | None = None,
        project_base_url: str | None = None,
        project_app_path: str = DEFAULT_PROJECT_APP_PATH,
        project_api_prefix: str = DEFAULT_PROJECT_API_PREFIX,
        login_encryption_source: str = "auto",
        timeout: float = 30.0,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self.identity_base_urls = unique_urls(
            [identity_base_url] if identity_base_url else [base_url, infer_dc_base_url(base_url)]
        )
        self.project_base_urls = unique_urls(
            [project_base_url] if project_base_url else [base_url, infer_dc_base_url(base_url)]
        )
        self.identity_base_url = self.identity_base_urls[0]
        self.project_base_url = self.project_base_urls[0]
        self.org_uuid = org_uuid
        self.team_uuid = team_uuid
        self.region_uuid = region_uuid
        self.project_app_path = normalize_api_prefix(project_app_path) or DEFAULT_PROJECT_APP_PATH
        self.project_api_prefix = normalize_api_prefix(project_api_prefix) or DEFAULT_PROJECT_API_PREFIX
        self.login_encryption_source = login_encryption_source
        self.timeout = timeout
        self._http_clients: dict[str, HttpClient] = {}
        self.identity_http = self._http_client_for(self.identity_base_url)
        self.project_http = self._http_client_for(self.project_base_url)
        self.access_token = ""

    def _http_client_for(self, base_url: str) -> HttpClient:
        normalized = normalize_base_url(base_url)
        client = self._http_clients.get(normalized)
        if client is None:
            client = HttpClient(normalized, timeout=self.timeout)
            self._http_clients[normalized] = client
        return client

    def _request_json_with_fallback(
        self,
        base_urls: list[str],
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        payload: Any | None = None,
        expected_status: set[int] | None = None,
        follow_redirects: bool = True,
    ) -> Any:
        errors: list[str] = []
        for index, candidate in enumerate(base_urls):
            client = self._http_client_for(candidate)
            try:
                response = client.request_json(
                    method,
                    path,
                    headers=headers,
                    payload=payload,
                    expected_status=expected_status,
                    follow_redirects=follow_redirects,
                )
                self.project_http = client
                self.project_base_url = candidate
                return response
            except ApiError as error:
                message = str(error)
                is_last = index == len(base_urls) - 1
                if "HTTP 404" not in message or is_last:
                    raise
                errors.append(message)

        raise ApiError(" | ".join(errors))

    def _auth_headers(self) -> dict[str, str]:
        headers = {
            "Accept-Language": DEFAULT_LANGUAGE,
            "Origin": self.base_url,
            "Referer": f"{self.base_url}{self.project_app_path}/",
        }
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def fetch_identity_encryption_cert(self) -> dict[str, Any]:
        return self.identity_http.request_json(
            "POST",
            "/identity/api/encryption_cert",
            headers={
                "Accept-Language": DEFAULT_LANGUAGE,
                "Origin": self.base_url,
                "Referer": f"{self.base_url}/auth/login?lang={DEFAULT_LANGUAGE}&org_uuid={self.org_uuid}",
            },
            payload={},
        )

    def fetch_login_support(self) -> dict[str, Any]:
        return self._request_json_with_fallback(
            self.project_base_urls,
            "GET",
            join_url_path(self.project_api_prefix, "/auth/login_support")
            + f"?org_uuid={urllib.parse.quote(self.org_uuid)}",
            headers={
                "Accept-Language": DEFAULT_LANGUAGE,
                "Referer": f"{self.base_url}/auth/login?lang={DEFAULT_LANGUAGE}&org_uuid={self.org_uuid}",
            },
        )

    def get_login_public_key(self) -> str:
        errors: list[str] = []
        mode = self.login_encryption_source.strip().lower()

        if mode not in {"auto", "identity_cert", "login_support"}:
            raise ApiError("ONES_LOGIN_ENCRYPTION_SOURCE 仅支持 auto / identity_cert / login_support。")

        if mode in {"auto", "identity_cert"}:
            try:
                cert = self.fetch_identity_encryption_cert()
                public_key = str(cert.get("public_key", "")).strip()
                if public_key:
                    return public_key
                errors.append("identity/encryption_cert 未返回 public_key")
            except ApiError as error:
                errors.append(str(error))
                if mode == "identity_cert":
                    raise

        if mode in {"auto", "login_support"}:
            try:
                support = self.fetch_login_support()
                public_key = (
                    support.get("encryption", {})
                    .get("encrypt_config", {})
                    .get("public_key", "")
                    .strip()
                )
                if public_key:
                    return public_key
                errors.append("project auth/login_support 未返回 encryption.encrypt_config.public_key")
            except ApiError as error:
                errors.append(str(error))
                if mode == "login_support":
                    raise

        raise ApiError("拿不到登录公钥。已尝试: " + " | ".join(errors))

    def login(self, admin_email: str, admin_password: str) -> LoginContext:
        public_key = self.get_login_public_key()
        encrypted_password = rsa_encrypt(public_key, admin_password)
        login_payload = {"email": admin_email, "password": encrypted_password}
        login_response = self.identity_http.request_json(
            "POST",
            "/identity/api/login",
            headers={
                "Accept-Language": DEFAULT_LANGUAGE,
                "Origin": self.base_url,
                "Referer": f"{self.base_url}/auth/login?lang={DEFAULT_LANGUAGE}&org_uuid={self.org_uuid}",
            },
            payload=login_payload,
        )

        org_user = self._pick_org_user(login_response)
        verifier, challenge = build_pkce_pair()
        authorize_response = self.identity_http.request_form(
            "POST",
            "/identity/authorize",
            headers={
                "Accept-Language": DEFAULT_LANGUAGE,
                "Origin": self.base_url,
                "Referer": (
                    f"{self.base_url}/auth/authorize?org_uuid={self.org_uuid}"
                    f"&region_uuid={org_user['region_uuid']}&org_user_uuid={org_user['org_user_uuid']}&ones_from="
                ),
            },
            form_data={
                "client_id": "ones.v1",
                "scope": (
                    f"openid offline_access "
                    f"ones:org:{org_user['region_uuid']}:{org_user['org_uuid']}:{org_user['org_user_uuid']}"
                ),
                "response_type": "code",
                "code_challenge_method": "S256",
                "code_challenge": challenge,
                "redirect_uri": f"{self.base_url}/auth/authorize/callback",
                "state": f"org_uuid={org_user['org_uuid']}",
            },
            expected_status={302},
            follow_redirects=False,
        )

        location = authorize_response.headers.get("Location", "")
        if not location:
            raise ApiError("identity/authorize 未返回跳转地址，无法继续交换 access token。")

        query = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
        codes = query.get("code", [])
        if not codes:
            raise ApiError("authorize 回调地址里没有 code 参数。")

        token_response = self.identity_http.request_form(
            "POST",
            "/identity/oauth/token",
            headers={
                "Accept-Language": DEFAULT_LANGUAGE,
                "Origin": self.base_url,
                "Referer": location,
            },
            form_data={
                "grant_type": "authorization_code",
                "client_id": "ones.v1",
                "code": codes[0],
                "code_verifier": verifier,
                "redirect_uri": f"{self.base_url}/auth/authorize/callback",
            },
        )

        token_payload = json.loads(token_response.body)
        access_token = str(token_payload.get("access_token", "")).strip()
        if not access_token:
            raise ApiError("oauth/token 未返回 access_token。")

        self.access_token = access_token
        for client in self._http_clients.values():
            client._set_cookie("ones-lt", access_token)
        return LoginContext(
            access_token=access_token,
            org_uuid=org_user["org_uuid"],
            region_uuid=org_user["region_uuid"],
            org_user_uuid=org_user["org_user_uuid"],
        )

    def _pick_org_user(self, login_response: dict[str, Any]) -> dict[str, str]:
        for item in login_response.get("org_users", []):
            if item.get("org_uuid") == self.org_uuid:
                org_user = item.get("org_user", {})
                return {
                    "org_uuid": item.get("org_uuid", self.org_uuid),
                    "region_uuid": item.get("region_uuid", self.region_uuid),
                    "org_user_uuid": org_user.get("org_user_uuid", ""),
                }

        org_users = login_response.get("org_users", [])
        if not org_users:
            raise ApiError("登录成功，但响应里没有 org_users。")

        org_user = org_users[0].get("org_user", {})
        return {
            "org_uuid": org_users[0].get("org_uuid", self.org_uuid),
            "region_uuid": org_users[0].get("region_uuid", self.region_uuid),
            "org_user_uuid": org_user.get("org_user_uuid", ""),
        }

    def invite_members(self, emails: list[str]) -> Any:
        return self._request_json_with_fallback(
            self.project_base_urls,
            "POST",
            join_url_path(self.project_api_prefix, f"/team/{self.team_uuid}/invitations/add_batch"),
            headers=self._auth_headers(),
            payload={
                "invite_settings": [{"email": email} for email in emails],
                "license_types": [],
                "action_name": "",
            },
        )

    def fetch_invitation_payload(self) -> Any:
        return self._request_json_with_fallback(
            self.project_base_urls,
            "GET",
            join_url_path(self.project_api_prefix, f"/team/{self.team_uuid}/invitations"),
            headers=self._auth_headers(),
        )

    def resolve_invitations(
        self,
        emails: list[str],
        *,
        retries: int,
        interval_seconds: float,
    ) -> list[InvitationRecord]:
        target_emails = set(emails)
        matches: dict[str, InvitationRecord] = {}

        for attempt in range(1, retries + 1):
            payload = self.fetch_invitation_payload()
            for record in extract_invitation_records(payload):
                if record.email in target_emails and record.email not in matches:
                    matches[record.email] = record

            if len(matches) == len(target_emails):
                break

            if attempt < retries:
                time.sleep(interval_seconds)

        ordered_records = [matches[email] for email in emails if email in matches]
        return ordered_records

    def activate_member(self, email: str, invite_code: str, member_password: str) -> Any:
        return self._request_json_with_fallback(
            self.project_base_urls,
            "POST",
            join_url_path(self.project_api_prefix, "/auth/invite_join_team"),
            headers={
                "Accept-Language": DEFAULT_LANGUAGE,
                "Origin": self.project_base_url,
                "Referer": (
                    f"{self.project_base_url}{self.project_app_path}/"
                    f"?org_uuid={self.org_uuid}&region_uuid={self.region_uuid}"
                ),
            },
            payload={
                "email": email,
                "name": email,
                "password": member_password,
                "invite_code": invite_code,
                "language": DEFAULT_LANGUAGE,
            },
        )


def nested_value(data: dict[str, Any], *paths: tuple[str, ...]) -> str:
    for path in paths:
        current: Any = data
        ok = True
        for key in path:
            if not isinstance(current, dict) or key not in current:
                ok = False
                break
            current = current[key]
        if ok and isinstance(current, str) and current.strip():
            return current.strip()
    return ""


def iterate_dicts(payload: Any) -> Any:
    if isinstance(payload, dict):
        yield payload
        for value in payload.values():
            yield from iterate_dicts(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from iterate_dicts(item)


def extract_invitation_records(payload: Any) -> list[InvitationRecord]:
    results: list[InvitationRecord] = []
    seen: set[tuple[str, str]] = set()

    for item in iterate_dicts(payload):
        email = nested_value(
            item,
            ("email",),
            ("invite_email",),
            ("user", "email"),
            ("invitee", "email"),
            ("member", "email"),
        )
        invite_code = nested_value(item, ("code",), ("invite_code",))
        invite_link = nested_value(item, ("invite_link",), ("link",), ("url",))

        if not email or not invite_code:
            continue

        key = (email, invite_code)
        if key in seen:
            continue
        seen.add(key)
        results.append(
            InvitationRecord(
                email=email,
                invite_code=invite_code,
                invite_link=invite_link,
                source="api",
            )
        )

    return results


def default_output_path(command: str) -> Path:
    return Path.cwd() / "outputs" / f"{command}-result.csv"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="批量邀请并激活 ONES 成员。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common_flags(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--base-url", default=env_default("ONES_BASE_URL", DEFAULT_BASE_URL))
        subparser.add_argument("--identity-base-url", default=env_default("ONES_IDENTITY_BASE_URL"))
        subparser.add_argument("--project-base-url", default=env_default("ONES_PROJECT_BASE_URL"))
        subparser.add_argument("--org-uuid", default=env_default("ONES_ORG_UUID", DEFAULT_ORG_UUID))
        subparser.add_argument("--team-uuid", default=env_default("ONES_TEAM_UUID", DEFAULT_TEAM_UUID))
        subparser.add_argument("--region-uuid", default=env_default("ONES_REGION_UUID", DEFAULT_REGION_UUID))
        subparser.add_argument("--project-app-path", default=env_default("ONES_PROJECT_APP_PATH", DEFAULT_PROJECT_APP_PATH))
        subparser.add_argument("--project-api-prefix", default=env_default("ONES_PROJECT_API_PREFIX", DEFAULT_PROJECT_API_PREFIX))
        subparser.add_argument(
            "--login-encryption-source",
            default=env_default("ONES_LOGIN_ENCRYPTION_SOURCE", "auto"),
            help="auto / identity_cert / login_support",
        )
        subparser.add_argument("--timeout", type=float, default=float(env_default("ONES_TIMEOUT", "30") or "30"))

    def add_admin_flags(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--admin-email", default=env_default("ONES_ADMIN_EMAIL"))
        subparser.add_argument("--admin-password", default=env_default("ONES_ADMIN_PASSWORD"))
        subparser.add_argument("--fetch-retries", type=int, default=int(env_default("ONES_FETCH_RETRIES", "5") or "5"))
        subparser.add_argument("--fetch-interval", type=float, default=float(env_default("ONES_FETCH_INTERVAL", "1") or "1"))

    invite_parser = subparsers.add_parser("invite", help="批量邀请成员并导出邀请码。")
    add_common_flags(invite_parser)
    add_admin_flags(invite_parser)
    invite_parser.add_argument("--count", type=int, required=True)
    invite_parser.add_argument("--email-prefix", default=env_default("ONES_EMAIL_PREFIX", DEFAULT_EMAIL_PREFIX))
    invite_parser.add_argument("--email-domain", default=env_default("ONES_EMAIL_DOMAIN", DEFAULT_EMAIL_DOMAIN))
    invite_parser.add_argument("--output", type=Path, default=None)

    activate_parser = subparsers.add_parser("activate", help="使用已有邀请码文件批量激活。")
    add_common_flags(activate_parser)
    activate_parser.add_argument("--input", type=Path, required=True)
    activate_parser.add_argument("--member-password", default=env_default("ONES_MEMBER_PASSWORD", DEFAULT_MEMBER_PASSWORD))
    activate_parser.add_argument("--output", type=Path, default=None)

    run_parser = subparsers.add_parser("run", help="邀请并立即激活。")
    add_common_flags(run_parser)
    add_admin_flags(run_parser)
    run_parser.add_argument("--count", type=int, required=True)
    run_parser.add_argument("--email-prefix", default=env_default("ONES_EMAIL_PREFIX", DEFAULT_EMAIL_PREFIX))
    run_parser.add_argument("--email-domain", default=env_default("ONES_EMAIL_DOMAIN", DEFAULT_EMAIL_DOMAIN))
    run_parser.add_argument("--member-password", default=env_default("ONES_MEMBER_PASSWORD", DEFAULT_MEMBER_PASSWORD))
    run_parser.add_argument("--output", type=Path, default=None)

    return parser


def require_admin_credentials(args: argparse.Namespace) -> None:
    if not args.admin_email or not args.admin_password:
        raise ApiError("需要提供 --admin-email 和 --admin-password，或设置 ONES_ADMIN_EMAIL / ONES_ADMIN_PASSWORD。")


def run_invite(args: argparse.Namespace) -> int:
    require_admin_credentials(args)
    if args.count <= 0:
        raise ApiError("--count 必须大于 0。")

    output_path = args.output or default_output_path("invite")
    emails = generate_random_emails(args.count, args.email_prefix, args.email_domain)
    client = OnesInviteClient(
        args.base_url,
        args.org_uuid,
        args.team_uuid,
        args.region_uuid,
        identity_base_url=args.identity_base_url,
        project_base_url=args.project_base_url,
        project_app_path=args.project_app_path,
        project_api_prefix=args.project_api_prefix,
        login_encryption_source=args.login_encryption_source,
        timeout=args.timeout,
    )

    client.login(args.admin_email, args.admin_password)
    client.invite_members(emails)
    records = client.resolve_invitations(
        emails,
        retries=args.fetch_retries,
        interval_seconds=args.fetch_interval,
    )

    missing = [email for email in emails if email not in {record.email for record in records}]
    save_records(output_path, records)

    print(f"已邀请 {len(emails)} 个成员，拿到 {len(records)} 条邀请码记录。")
    print(f"结果文件: {output_path}")
    if missing:
        print("未解析到邀请码的邮箱:")
        for email in missing:
            print(f"  - {email}")
        return 1
    return 0


def activate_records(
    client: OnesInviteClient,
    records: list[InvitationRecord],
    member_password: str,
) -> tuple[int, list[InvitationRecord]]:
    success_count = 0
    updated: list[InvitationRecord] = []

    for record in records:
        try:
            activation_response = client.activate_member(record.email, record.invite_code, member_password)
            record.activated = True
            record.activation_status = "success"
            record.activation_message = serialize_message(activation_response)
            success_count += 1
        except ApiError as error:
            record.activated = False
            record.activation_status = "failed"
            record.activation_message = str(error)
        updated.append(record)

    return success_count, updated


def run_activate(args: argparse.Namespace) -> int:
    records = load_records_from_file(args.input)
    if not records:
        raise ApiError("输入文件里没有可激活的邀请码记录。")

    output_path = args.output or default_output_path("activate")
    client = OnesInviteClient(
        args.base_url,
        args.org_uuid,
        args.team_uuid,
        args.region_uuid,
        identity_base_url=args.identity_base_url,
        project_base_url=args.project_base_url,
        project_app_path=args.project_app_path,
        project_api_prefix=args.project_api_prefix,
        login_encryption_source=args.login_encryption_source,
        timeout=args.timeout,
    )
    success_count, updated = activate_records(client, records, args.member_password)
    save_records(output_path, updated)

    print(f"已激活 {success_count}/{len(records)} 个成员。")
    print(f"结果文件: {output_path}")
    return 0 if success_count == len(records) else 1


def run_all(args: argparse.Namespace) -> int:
    require_admin_credentials(args)
    if args.count <= 0:
        raise ApiError("--count 必须大于 0。")

    output_path = args.output or default_output_path("run")
    emails = generate_random_emails(args.count, args.email_prefix, args.email_domain)
    client = OnesInviteClient(
        args.base_url,
        args.org_uuid,
        args.team_uuid,
        args.region_uuid,
        identity_base_url=args.identity_base_url,
        project_base_url=args.project_base_url,
        project_app_path=args.project_app_path,
        project_api_prefix=args.project_api_prefix,
        login_encryption_source=args.login_encryption_source,
        timeout=args.timeout,
    )

    client.login(args.admin_email, args.admin_password)
    client.invite_members(emails)
    records = client.resolve_invitations(
        emails,
        retries=args.fetch_retries,
        interval_seconds=args.fetch_interval,
    )

    resolved_emails = {record.email for record in records}
    missing = [email for email in emails if email not in resolved_emails]
    success_count, updated = activate_records(client, records, args.member_password)
    save_records(output_path, updated)

    print(f"邀请 {len(emails)} 个成员，解析到 {len(records)} 条邀请码，激活成功 {success_count} 个。")
    print(f"结果文件: {output_path}")
    if missing:
        print("未解析到邀请码的邮箱:")
        for email in missing:
            print(f"  - {email}")

    has_failures = missing or success_count != len(records)
    return 1 if has_failures else 0


def main() -> int:
    load_project_env(Path.cwd() / ".env")
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "invite":
            return run_invite(args)
        if args.command == "activate":
            return run_activate(args)
        if args.command == "run":
            return run_all(args)
        raise ApiError(f"不支持的命令: {args.command}")
    except ApiError as error:
        print(f"错误: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
