from __future__ import annotations

import argparse

from .app import preview, run


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="artwall",
        description="Set a random Met Museum painting as the Sway wallpaper.",
    )
    parser.add_argument(
        "--min-interval",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="Skip the change if the previous one happened less than SECONDS ago. "
        "Use this when triggering from frequent Sway events (e.g. window focus) so "
        "the wallpaper rotates at most every SECONDS instead of on every event.",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Generate a captioned painting and open it with xdg-open, "
        "without changing the wallpaper.",
    )
    args = parser.parse_args(argv)

    if args.preview:
        preview()
    else:
        run(min_interval=args.min_interval)


if __name__ == "__main__":
    main()
