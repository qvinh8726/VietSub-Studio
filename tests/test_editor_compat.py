"""Application CapCut compatibility is explicit and never claims live readiness."""
import base64
import unittest
from unittest.mock import patch

from vietsub.editor.compat_signer import experimental_payload_signer, public_numbers
from vietsub.editor.providers import service


class CompatibilityTests(unittest.TestCase):
    def test_public_key_envelope_is_randomized_and_not_authentication(self):
        n, e = public_numbers()
        self.assertEqual(n.bit_length(), 2048)
        self.assertEqual(e, 65537)
        device = {'aid':'359289','device_id':'12345678901234567890'}
        first = experimental_payload_signer('<speak>test</speak>', '{}', device)
        second = experimental_payload_signer('<speak>test</speak>', '{}', device)
        self.assertEqual(len(base64.b64decode(first)), 256)
        self.assertNotEqual(first, second)

    def test_artifact_dns_private_answers_rejected_before_connect(self):
        from vietsub.editor.capcut_tts import _PublicHTTPSConnection, CapCutProtocolError
        with (patch('socket.getaddrinfo', return_value=[(2,1,6,'',('127.0.0.1',443))]),
              patch('socket.socket') as socket):
            with self.assertRaises(CapCutProtocolError):
                _PublicHTTPSConnection('127.0.0.1.nip.io', timeout=1).connect()
            socket.assert_not_called()

    def test_blank_runtime_is_normalized_and_tts_cache_rejects_junction(self):
        from pathlib import Path
        import tempfile
        from vietsub.manifest import ManifestError
        with tempfile.TemporaryDirectory() as root:
            cache = str(Path(root)/'cache')
            tts = service('ffmpeg','ffprobe',cache,runtime_python='')
            self.assertIsNone(tts.providers['edge_tts'].runtime_python)
            with patch('os.path.islink', side_effect=lambda path: path == cache):
                with self.assertRaises(ManifestError):
                    tts._cache('Xin chào.', 'BV421_vivn_streaming', 'capcut_internal', 1, 0)
            self.assertFalse(Path(cache).exists())

    def test_default_capcut_no_network_and_optin_only_registers_candidate(self):
        with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('no network')):
            standard = service('ffmpeg','ffprobe','unused-cache', config={'editor_capcut_compat': False})
            experimental = service('ffmpeg','ffprobe','unused-cache')
            self.assertFalse(standard.availability()['available'])
            self.assertTrue(experimental.availability()['configured'])
            self.assertFalse(experimental.availability()['service_verified'])
            self.assertTrue(standard.voices())


if __name__ == '__main__':
    unittest.main()
