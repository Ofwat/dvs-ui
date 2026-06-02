import unittest
from unittest.mock import patch

from refactor.services.data_validation_api.examples import common


class ExamplesCommonTests(unittest.TestCase):
    @patch("refactor.services.data_validation_api.examples.common.require_ok")
    @patch("refactor.services.data_validation_api.examples.common.check_token_access")
    def test_ensure_authenticated_uses_normal_token_check(self, check_token_access, require_ok):
        check_token_access.return_value = (True, {"status": "ok"})

        common.ensure_authenticated()

        check_token_access.assert_called_once_with()
        require_ok.assert_called_once_with("check_token_access", True, {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
