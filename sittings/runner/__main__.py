"""Serve hosted decision sittings; this command never dispatches agents."""

import argparse

from sittings.runner.server import RunnerConfig, RunnerServer


def _binding_hook(record, relay_log):
    from sittings.model_binding import build_binding

    return build_binding(record, relay_log)


def _review_hook(queue):
    def enqueue_or_reuse(record, receipt):
        try:
            return queue.show(record["sitting_id"])["status"]["state"]
        except FileNotFoundError:
            return queue.enqueue(record, receipt)

    return enqueue_or_reuse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--ttl-seconds", type=float, default=7200)
    parser.add_argument("--create-rate-limit", type=int, default=10)
    parser.add_argument("--rate-window-seconds", type=float, default=3600)
    parser.add_argument("--max-open-sittings", type=int, default=100)
    parser.add_argument("--max-stored-bytes", type=int, default=256 * 1024 * 1024)
    parser.add_argument("--max-request-concurrency", type=int, default=32)
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--behind-tls-proxy", action="store_true")
    parser.add_argument(
        "--key", required=True,
        help="private key for an operator-signed T1 receipt (CheckSeal DSSE + Ed25519)",
    )
    parser.add_argument(
        "--relay-provider", action="append", default=[], metavar="NAME=HTTPS_BASE_URL",
        help="allowlisted host-relay provider, such as NAME=https://host/v1",
    )
    args = parser.parse_args(argv)
    try:
        providers = {}
        for item in args.relay_provider:
            name, separator, url = item.partition("=")
            if not separator or not name or name in providers:
                parser.error("--relay-provider must be unique NAME=HTTPS_BASE_URL")
            providers[name] = url
        from sittings.receipt.sign import load_signer, seal_record
        from sittings.review.queue import ReviewQueue

        signer = load_signer(args.key)
        queue = ReviewQueue(args.data_dir)
        config = RunnerConfig(
            data_dir=args.data_dir,
            ttl_seconds=args.ttl_seconds,
            create_rate_limit=args.create_rate_limit,
            rate_window_seconds=args.rate_window_seconds,
            max_open_sittings=args.max_open_sittings,
            max_stored_bytes=args.max_stored_bytes,
            max_request_concurrency=args.max_request_concurrency,
            allow_partial=args.allow_partial,
            behind_tls_proxy=args.behind_tls_proxy,
            relay_providers=providers,
            binding_hook=_binding_hook,
            receipt_hook=lambda record: seal_record(record, signer),
            review_hook=_review_hook(queue),
        )
    except ValueError as exc:
        parser.error(str(exc))
        return 2  # parser.error exits; keep config unreachable on this path for static analysis.
    with RunnerServer((args.host, args.port), config) as server:
        print(
            f"OPERANT sitting runner listening on {args.host}:{server.server_port}",
            flush=True,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            # Ctrl-C ends serving normally; the context manager closes the listening socket.
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
