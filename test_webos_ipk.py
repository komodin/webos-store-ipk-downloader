import argparse
import ssl
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import webos_ipk


class RegionalRequestTests(unittest.TestCase):
    def test_computer_initialization_uses_device_headers_and_ssl_context(self):
        args = argparse.Namespace(init_from_computer=True, timeout=12)
        headers = {"X-Authentication": "device-token"}
        context = object()
        with (
            mock.patch.object(webos_ipk, "http_json", return_value={"sessionID": "session"}) as http,
            mock.patch.object(webos_ipk, "request_from_tv") as tv,
        ):
            session = webos_ipk.create_regional_session(args, headers, "DE", context)

        self.assertEqual(session, "session")
        http.assert_called_once_with(
            "https://DE.lgtvsdp.com/rest/sdp/v11.2/initservices",
            headers=headers,
            data={"api_info": "auth:Y,provisioning:N,services:N,appupdatecheck:N"},
            timeout=12,
            ssl_context=context,
        )
        tv.assert_not_called()

    def test_tv_remains_default_initialization_route(self):
        args = argparse.Namespace(init_from_computer=False)
        with (
            mock.patch.object(webos_ipk, "request_from_tv", return_value={"sessionID": "session"}) as tv,
            mock.patch.object(webos_ipk, "http_json") as http,
        ):
            session = webos_ipk.create_regional_session(args, {}, "DE", None)

        self.assertEqual(session, "session")
        tv.assert_called_once()
        http.assert_not_called()

    def test_insecure_tv_request_disables_curl_verification(self):
        args = argparse.Namespace(
            insecure=True, ssh_port=22, timeout=5, identity_file=None,
            ssh_option=[], tv="root@tv.local",
        )
        result = mock.Mock(returncode=0, stdout=b'{"sessionID":"session"}', stderr=b"")
        with mock.patch.object(webos_ipk.subprocess, "run", return_value=result) as run:
            response = webos_ipk.request_from_tv(args, "https://example.test", {}, {})

        self.assertEqual(response["sessionID"], "session")
        self.assertIn("insecure\n", run.call_args.kwargs["input"].decode())


class CertificateTests(unittest.TestCase):
    def test_verified_tls_is_default(self):
        args = argparse.Namespace(insecure=False, ca_bundle=None)
        self.assertIsNone(webos_ipk.ssl_context_for(args))

    def test_custom_ca_bundle_is_loaded(self):
        args = argparse.Namespace(insecure=False, ca_bundle=Path("certs.pem"))
        with mock.patch.object(ssl, "create_default_context") as create:
            self.assertIs(webos_ipk.ssl_context_for(args), create.return_value)
        create.assert_called_once_with(cafile="certs.pem")

    def test_insecure_context_disables_verification(self):
        args = argparse.Namespace(insecure=True, ca_bundle=None)
        self.assertEqual(webos_ipk.ssl_context_for(args).verify_mode, ssl.CERT_NONE)

    def test_http_json_passes_context_and_reports_certificate_failure(self):
        context = object()
        failure = urllib.error.URLError(ssl.SSLCertVerificationError("invalid chain"))
        with mock.patch.object(webos_ipk.urllib.request, "urlopen", side_effect=failure) as open_url:
            with self.assertRaisesRegex(webos_ipk.ToolError, "--ca-bundle"):
                webos_ipk.http_json("https://example.test", timeout=5, ssl_context=context)
        self.assertIs(open_url.call_args.kwargs["context"], context)

    def test_download_uses_context_and_removes_partial_on_tls_failure(self):
        context = object()
        failure = urllib.error.URLError(ssl.SSLCertVerificationError("invalid chain"))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "app.ipk"
            with mock.patch.object(webos_ipk.urllib.request, "urlopen", side_effect=failure) as open_url:
                with self.assertRaisesRegex(webos_ipk.ToolError, "--ca-bundle"):
                    webos_ipk.download("https://example.test/app.ipk", output, None, 5, False, context)
            self.assertFalse(output.with_name("app.ipk.part").exists())
        self.assertIs(open_url.call_args.kwargs["context"], context)


if __name__ == "__main__":
    unittest.main()
