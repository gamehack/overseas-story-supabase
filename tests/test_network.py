import os
import unittest
import urllib.request
from unittest.mock import patch

from overseas_stories.network import build_direct_opener, direct_subprocess_env


class OutboundNetworkTests(unittest.TestCase):
    def test_ambient_proxy_is_disabled_by_default_for_urllib(self):
        with patch.dict(os.environ, {"OUTBOUND_PROXY_ENABLED": "false"}, clear=False), \
             patch("overseas_stories.network.urllib.request.build_opener") as build:
            build_direct_opener()
        proxy_handler = build.call_args.args[0]
        self.assertIsInstance(proxy_handler, urllib.request.ProxyHandler)
        self.assertEqual(proxy_handler.proxies, {})

    def test_proxy_can_be_explicitly_enabled_for_local_networks(self):
        with patch.dict(os.environ, {"OUTBOUND_PROXY_ENABLED": "true"}, clear=False), \
             patch("overseas_stories.network.urllib.request.build_opener") as build:
            build_direct_opener()
        self.assertFalse(any(
            isinstance(handler, urllib.request.ProxyHandler)
            for handler in build.call_args.args
        ))

    def test_subprocess_environment_removes_proxy_variables_by_default(self):
        with patch.dict(os.environ, {
            "OUTBOUND_PROXY_ENABLED": "false",
            "HTTP_PROXY": "http://proxy.invalid:8080",
            "HTTPS_PROXY": "http://proxy.invalid:8080",
            "http_proxy": "http://proxy.invalid:8080",
        }, clear=True):
            env = direct_subprocess_env()
        self.assertNotIn("HTTP_PROXY", env)
        self.assertNotIn("HTTPS_PROXY", env)
        self.assertNotIn("http_proxy", env)

    def test_subprocess_environment_preserves_proxy_when_explicitly_enabled(self):
        with patch.dict(os.environ, {
            "OUTBOUND_PROXY_ENABLED": "true",
            "HTTPS_PROXY": "http://proxy.invalid:8080",
        }, clear=True):
            env = direct_subprocess_env()
        self.assertEqual(env["HTTPS_PROXY"], "http://proxy.invalid:8080")


if __name__ == "__main__":
    unittest.main()
