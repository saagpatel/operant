"""Serve hosted decision sittings; this command never dispatches agents."""

import argparse

from sittings.runner.server import RunnerConfig, RunnerServer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--ttl-seconds", type=float, default=7200)
    parser.add_argument("--create-rate-limit", type=int, default=10)
    parser.add_argument("--rate-window-seconds", type=float, default=3600)
    parser.add_argument("--max-open-sittings", type=int, default=100)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = RunnerConfig(
            data_dir=args.data_dir,
            ttl_seconds=args.ttl_seconds,
            create_rate_limit=args.create_rate_limit,
            rate_window_seconds=args.rate_window_seconds,
            max_open_sittings=args.max_open_sittings,
            allow_partial=args.allow_partial,
        )
    except ValueError as exc:
        parser.error(str(exc))
    with RunnerServer((args.host, args.port), config) as server:
        print(
            f"OPERANT sitting runner listening on {args.host}:{server.server_port}",
            flush=True,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
