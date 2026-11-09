"""Client transport policy regressions without network access."""

import contextlib
import io
import unittest
from unittest.mock import Mock, patch

from sittings import client


class ClientTransportTests(unittest.TestCase):
    def test_remote_http_rejected_before_any_request(self):
        # Only the three literal loopback hosts qualify, never lookalikes or DNS aliases.
        for host in ("runner.example", "127.0.0.2", "localhost.example", "127.1"):
            for relay in (None, {"api_key": "fixture-secret"}):
                for override in (False, True) if relay else (False,):
                    with self.subTest(host=host, relay=relay, override=override):
                        with patch.object(client, "request_json") as request:
                            with self.assertRaises(ValueError):
                                client.run_sitting(
                                    f"http://{host}", "test", "model",
                                    Mock() if relay is None else None, relay=relay,
                                    allow_insecure_runner=override,
                                )
                            request.assert_not_called()

    def test_https_and_literal_loopback_http_accept_both_modes(self):
        sitting_id = "sit_" + "a" * 26
        result = {"sitting_id": sitting_id, "record": {"sitting_id": sitting_id}}
        for url in ("https://runner.example", "http://127.0.0.1:8765",
                    "http://[::1]:8765", "http://localhost:8765"):
            for relay in (None, {"api_key": "fixture-secret"}):
                with self.subTest(url=url, relay=relay):
                    replies = [{"sitting_id": sitting_id, "token": "fixture-token"}]
                    if relay is None:
                        replies.append({"done": True})
                    replies.append(result)
                    with patch.object(client, "request_json", side_effect=replies) as request:
                        self.assertEqual(client.run_sitting(
                            url, "test", "model", Mock() if relay is None else None,
                            relay=relay,
                        ), result)
                    self.assertEqual(request.call_args.kwargs["token"], "fixture-token")
                    self.assertEqual(request.call_args_list[0].kwargs["body"].get("relay"), relay)

    def test_cli_insecure_flag_allows_only_unbound_runs(self):
        args = ["--runner", "http://runner.example", "--label", "test",
                "--claimed-model", "model"]
        for relay in (False, True):
            source = (["--relay-provider", "test", "--relay-model", "model",
                       "--relay-key-env", "UNUSED_FIXTURE_KEY"] if relay
                      else ["--adapter", "examples/heuristic_agent.py:respond"])
            for override in (False, True):
                with self.subTest(relay=relay, override=override):
                    with (patch.object(client, "run_sitting") as run,
                          patch.object(client, "make_runner") as make,
                          contextlib.redirect_stderr(io.StringIO()),
                          contextlib.redirect_stdout(io.StringIO())):
                        run.return_value = {"sitting_id": "fixture", "record": {"score": {}},
                                            "receipt": None}
                        command = args + source + (["--allow-insecure-runner"] if override else [])
                        if override and not relay:
                            self.assertEqual(client.main(command), 0)
                            self.assertTrue(run.call_args.kwargs["allow_insecure_runner"])
                        else:
                            with self.assertRaises(SystemExit) as error:
                                client.main(command)
                            self.assertEqual(error.exception.code, 2)
                            run.assert_not_called()
                            make.assert_not_called()

    def test_explicit_insecure_unbound_run_completes(self):
        sitting_id = "sit_" + "a" * 26
        result = {"sitting_id": sitting_id, "record": {"sitting_id": sitting_id}}
        with patch.object(client, "request_json", side_effect=[
            {"sitting_id": sitting_id, "token": "fixture-token"}, {"done": True}, result,
        ]) as request:
            self.assertEqual(client.run_sitting(
                "http://runner.example", "test", "model", Mock(), allow_insecure_runner=True,
            ), result)
        self.assertEqual(request.call_count, 3)


if __name__ == "__main__":
    unittest.main()
