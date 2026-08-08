from noiz_pseo_voice.cms import CmsClient, CmsError


def test_verify_credentials_login_success(monkeypatch):
    client = CmsClient(
        "https://example.invalid/seo-manage",
        email="user@example.com",
        password="secret",
    )
    monkeypatch.setattr(client, "_login", lambda: "jwt")
    client.verify_credentials()  # no raise


def test_verify_credentials_login_failure(monkeypatch):
    client = CmsClient(
        "https://example.invalid/seo-manage",
        email="bad@example.com",
        password="wrong",
    )

    def boom():
        raise CmsError("CMS login HTTP 401")

    monkeypatch.setattr(client, "_login", boom)
    try:
        client.verify_credentials()
    except CmsError as exc:
        assert "401" in str(exc)
        return
    raise AssertionError("bad login should raise")


def test_verify_credentials_api_key_probe(monkeypatch):
    client = CmsClient("https://example.invalid/seo-manage", api_key="k")
    monkeypatch.setattr(client, "_request", lambda path, params=None: {"user": {}})
    client.verify_credentials()  # no raise


def test_verify_credentials_missing_raises():
    client = CmsClient("https://example.invalid/seo-manage")
    try:
        client.verify_credentials()
    except CmsError as exc:
        assert "no CMS credentials" in str(exc)
        return
    raise AssertionError("missing credentials should raise")
