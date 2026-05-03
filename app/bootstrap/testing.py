from __future__ import annotations

from fastapi import FastAPI


class _CompatResponse:
    def __init__(self, response):
        self._response = response
        self.status_code = response.status_code
        self.headers = response.headers

    def get_json(self):
        return self._response.json()

    def get_data(self, *, as_text: bool = False):
        return self._response.text if as_text else self._response.content


class _CompatSessionTransaction:
    def __init__(self, client: "_CompatTestClient"):
        self.client = client

    def __enter__(self):
        return self.client._session

    def __exit__(self, exc_type, exc, tb):
        return False


class _CompatTestClient:
    def __init__(self, fastapi_app: FastAPI):
        from fastapi.testclient import TestClient

        self._client = TestClient(fastapi_app, follow_redirects=False)
        self._session: dict[str, int] = {}

    def session_transaction(self):
        return _CompatSessionTransaction(self)

    def get(self, url: str, **kwargs):
        return self._request("GET", url, **kwargs)

    def post(self, url: str, **kwargs):
        return self._request("POST", url, **kwargs)

    def _request(self, method: str, url: str, **kwargs):
        from app import app as legacy

        kwargs.pop("content_type", None)
        follow_redirects = kwargs.pop("follow_redirects", False)
        headers = dict(kwargs.pop("headers", {}) or {})
        if legacy.USER_SESSION_KEY in self._session:
            headers["x-test-user-id"] = str(self._session[legacy.USER_SESSION_KEY])

        data = kwargs.pop("data", None)
        files = kwargs.pop("files", None)
        if data is not None and files is None:
            form_data = {}
            converted_files = {}
            for key, value in dict(data).items():
                if isinstance(value, tuple) and len(value) >= 2:
                    file_obj, filename = value[:2]
                    converted_files[key] = (filename, file_obj)
                else:
                    form_data[key] = value
            data = form_data
            files = converted_files or None

        response = self._client.request(
            method,
            url,
            data=data,
            files=files,
            headers=headers,
            follow_redirects=follow_redirects,
            **kwargs,
        )
        request_path = url.split("?", 1)[0]
        if request_path == "/logout" or (request_path.startswith("/reset-password/") and response.status_code in {302, 303}):
            self._session.clear()
        return _CompatResponse(response)


def install_test_client(fastapi_app: FastAPI) -> None:
    def _test_client():
        return _CompatTestClient(fastapi_app)

    fastapi_app.test_client = _test_client
