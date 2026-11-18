import io
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

from tools import cloud_auth


ACCOUNT = "a" * 32
TOKEN_ID = "b" * 32


class CloudAuthTests(unittest.TestCase):
    def test_oauth_scope_and_token_groups_cover_same_products(self):
        groups = []
        expected = []
        for index, (product, scope) in enumerate(cloud_auth.PERMISSIONS.items()):
            for operation, offset in (("Read", 0), ("Write", 1)):
                self.assertIn(f"{scope}.{operation.lower()}", cloud_auth.OAUTH_SCOPES)
                identifier = f"{index * 2 + offset:032x}"
                group = {"id": identifier, "name": f"{product} {operation}",
                         "scopes": [cloud_auth.ACCOUNT_SCOPE], "is_selectable": True}
                expected.append(identifier)
                groups.append(group)
                groups.append(dict(group, id="f" * 32, scopes=[cloud_auth.ACCOUNT_SCOPE + ".zone"]))
        self.assertEqual(cloud_auth.permission_ids(groups), expected)
        groups[-2]["is_selectable"] = False
        with self.assertRaisesRegex(cloud_auth.AuthError, "Access: Service Tokens Write"):
            cloud_auth.permission_ids(groups)

    def test_issued_token_policy_must_bind_expected_groups_and_one_account(self):
        policy = {
            "effect": "allow", "resources": {f"{cloud_auth.ACCOUNT_SCOPE}.{ACCOUNT}": "*"},
            "permission_groups": [{"id": TOKEN_ID}],
        }
        cloud_auth.check_token_policy({"policies": [policy]}, ACCOUNT, [TOKEN_ID])
        with self.assertRaises(cloud_auth.AuthError):
            cloud_auth.check_token_policy({"policies": [policy]}, ACCOUNT, ["c" * 32])
        policy["resources"] = {cloud_auth.ACCOUNT_SCOPE: "*"}
        with self.assertRaises(cloud_auth.AuthError):
            cloud_auth.check_token_policy({"policies": [policy]}, ACCOUNT, [TOKEN_ID])

    def test_device_flow_prints_prefilled_official_link(self):
        with patch.object(cloud_auth.subprocess, "Popen") as popen, redirect_stdout(io.StringIO()) as output:
            process = MagicMock(stdout=io.StringIO(
                "  https://dash.cloudflare.com/oauth2/device/verify\n\n"
                "and enter the code:\n\n  a1B-2Cd3\n"
            ))
            process.wait.return_value = 0
            process.poll.return_value = 0
            popen.return_value.__enter__.return_value = process
            cloud_auth.show_device_login({}, Path("."))
        self.assertIn(f"{cloud_auth.DEVICE_URL}?user_code=a1B-2Cd3", output.getvalue())
        self.assertEqual(output.getvalue().count(cloud_auth.DEVICE_URL), 1)
        self.assertEqual(popen.call_args.args[0][-len(cloud_auth.OAUTH_SCOPES):], list(cloud_auth.OAUTH_SCOPES))

    def test_token_verification_stops_if_api_returns_a_different_token(self):
        with patch.object(cloud_auth, "api_get", return_value={"status": "active", "id": "c" * 32}) as api:
            with self.assertRaisesRegex(cloud_auth.AuthError, "expected active account token"):
                cloud_auth.verify({"account_id": ACCOUNT, "token_id": TOKEN_ID, "token": "synthetic"})
            api.assert_called_once()

    def test_credentials_are_private_and_cannot_be_loaded_from_a_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            target = folder / "cloud-auth.json"
            data = {"account_id": ACCOUNT, "confirmation": f"dedicated-lab:{ACCOUNT}",
                    "token_id": TOKEN_ID, "token": "x" * 40}
            with patch.multiple(cloud_auth, LAB=folder, CREDENTIALS=target):
                cloud_auth.store(data)
            self.assertEqual(cloud_auth.read_credentials(target), data)
            link = folder / "link.json"
            link.symlink_to(target)
            with self.assertRaisesRegex(cloud_auth.AuthError, "private regular file"):
                cloud_auth.read_credentials(link)
