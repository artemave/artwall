from __future__ import annotations

import argparse

from .app import preview, run, search_entities


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="artwall",
        description="Set a random Wikidata painting as the Sway wallpaper.",
    )
    parser.add_argument(
        "--throttle",
        action="store_true",
        help="Skip the change if the previous one happened less than the configured "
        "interval ago (Config.min_interval). Use this when triggering from frequent "
        "Sway events (e.g. window focus) so the wallpaper rotates at most that often "
        "instead of on every event.",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Generate a captioned painting and open it with xdg-open, "
        "without changing the wallpaper.",
    )
    parser.add_argument(
        "--find",
        metavar="TERM",
        help="Look up Wikidata QIDs for a name (artist, movement, genre, museum) "
        "to drop into the config's filters, then exit.",
    )
    args = parser.parse_args(argv)

    if args.find:
        for qid, label, description in search_entities(args.find):
            print(f"{qid}\t{label} — {description}")
    elif args.preview:
        preview()
    else:
        run(throttle=args.throttle)


if __name__ == "__main__":
    main()
