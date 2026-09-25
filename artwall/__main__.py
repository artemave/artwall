from __future__ import annotations

import argparse

from . import stars
from .app import preview, run, search_entities


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="artwall",
        description="Set a random Wikidata painting as the Sway or KDE Plasma wallpaper.",
    )
    parser.add_argument(
        "--throttle",
        action="store_true",
        help="Skip the change if the previous one happened less than the configured "
        "interval ago (Config.min_interval, or --min-interval). Use this when triggering "
        "frequently (Sway window events, or a timer loop on Plasma) so the wallpaper "
        "rotates at most that often instead of on every trigger.",
    )
    parser.add_argument(
        "--min-interval",
        type=float,
        metavar="SECONDS",
        help="Override Config.min_interval for --throttle. Use a small value (e.g. 5) on "
        "the output-event subscription to coalesce the burst of events a single monitor "
        "hotplug fires, while window events keep the long interval.",
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
    parser.add_argument(
        "--output",
        metavar="NAME",
        help="Re-roll only the display with this output name, instead of every "
        "connected display. Used by the interactive overlay's refresh button.",
    )
    parser.add_argument(
        "--star",
        metavar="NAME",
        help="Star the painting currently on this output — archiving the image "
        "alongside the gallery — or unstar it if it's already there. Used by the "
        "interactive overlay's star button.",
    )
    args = parser.parse_args(argv)

    # --min-interval only tunes the --throttle check; on its own it's a silent
    # no-op (the throttle branch never runs), so reject the combo loudly.
    if args.min_interval is not None and not args.throttle:
        parser.error("--min-interval has no effect without --throttle")

    if args.find:
        for qid, label, description in search_entities(args.find):
            print(f"{qid}\t{label} — {description}")
    elif args.preview:
        preview()
    elif args.star:
        starred = stars.star(output=args.star)
        print("starred" if starred else "unstarred")
    else:
        run(throttle=args.throttle, min_interval=args.min_interval, only=args.output)


if __name__ == "__main__":
    main()
