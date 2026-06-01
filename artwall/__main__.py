from __future__ import annotations

import argparse

from .app import preview, run


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="artwall",
        description="Set a random Met Museum painting as the Sway wallpaper.",
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
        run()


if __name__ == "__main__":
    main()
