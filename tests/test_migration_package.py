import unittest
from unittest.mock import patch
from redash.migration_package import seal, unseal, MAX_FILE_BYTES, MAX_CONTENT_BYTES


class TestMigrationEnvelope(unittest.TestCase):
    def test_roundtrip_random_salt_and_no_plaintext(self):
        value = {'name': 'PRIVATE_PASSWORD_MARKER', 'nested': [1, True, None]}
        a = seal(value, 'synthetic-passphrase')
        b = seal(value, 'synthetic-passphrase')
        self.assertNotEqual(a, b)
        self.assertNotIn(value['name'], str(a))
        self.assertEqual(unseal(a, 'synthetic-passphrase'), value)

    def test_wrong_password_and_modified_ciphertext(self):
        envelope = seal({'secret': 'PRIVATE_MARKER'}, 'synthetic-passphrase')
        for package, password in ((envelope, 'wrong-synthetic-passphrase'),
                ({**envelope, 'ciphertext': envelope['ciphertext'][:-5] + 'xxxxx'}, 'synthetic-passphrase')):
            with self.assertRaisesRegex(ValueError, 'Unable to open package'):
                unseal(package, password)

    def test_untrusted_work_factors_and_oversize_rejected_before_kdf(self):
        envelope = seal({}, 'synthetic-passphrase')
        for package in ({**envelope, 'iterations': 999999999}, {**envelope, 'version': True},
                        {**envelope, 'salt': 'invalid'}, {**envelope, 'ciphertext': 'x' * (MAX_FILE_BYTES + 1)}):
            with patch('redash.migration_package._cipher') as cipher:
                with self.assertRaises(ValueError): unseal(package, 'synthetic-passphrase')
                cipher.assert_not_called()

    def test_invalid_passphrases_and_nonfinite_content(self):
        for password in ('short', None, 17, 'x' * 1025):
            with self.assertRaises(ValueError): seal({}, password)
        for number in (float('inf'), float('nan'), -float('inf')):
            with self.assertRaises(ValueError): seal({'value': number}, 'synthetic-passphrase')

    def test_depth_and_content_limits(self):
        value = {}
        for _ in range(42): value = {'nested': value}
        for content in (value, {'text': 'x' * MAX_CONTENT_BYTES}):
            with self.assertRaises(ValueError): seal(content, 'synthetic-passphrase')
