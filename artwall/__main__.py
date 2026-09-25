from __future__ import annotations

import argparse

from . import stars
from .app import preview, run, search_entities


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="artwall",
        description="Rotate random Wikidata paintings on the Sway or KDE Plasma wallpaper. "
        "With no action, runs artwall for the session: the caption on each display, "
        "rotation, and the starred gallery.",
    )
    parser.add_argument(
        "--serve-stars",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Host the starred gallery for as long as artwall runs, so the gallery "
        "button opens the live, editable page. With --no-serve-stars it opens the "
        "archived, read-only stars.html instead, and nothing listens on a port.",
    )
    parser.add_argument(
        "--publish-stars",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep the uploadable static site under the data directory's public/ in "
        "step with the collection — rebuilt at startup and on every star, unstar, "
        "restore and paste. --no-publish-stars builds no site at all.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Set a new painting on every display, then exit.",
    )
    parser.add_argument(
        "--throttle",
        action="store_true",
        help="With --once: skip the change if the previous one happened less than the "
        "configured interval ago (Config.min_interval, or --min-interval). artwall's "
        "rotation timer runs this every minute.",
    )
    parser.add_argument(
        "--min-interval",
        type=float,
        metavar="SECONDS",
        help="Override Config.min_interval for --throttle. artwall uses 5 on monitor "
        "hotplug, so connecting several screens at once re-rolls just once.",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Generate a random painting and open it with xdg-open, "
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
        help="Re-roll only the display with this output name. Used by the caption's "
        "refresh button.",
    )
    parser.add_argument(
        "--star",
        metavar="NAME",
        help="Star the painting currently on this output — archiving the image "
        "alongside the gallery — or unstar it if it's already there. Used by the "
        "caption's star button.",
    )
    args = parser.parse_args(argv)

    # Each would otherwise be a silent no-op, so reject the combo loudly.
    if args.min_interval is not None and not args.throttle:
        parser.error("--min-interval has no effect without --throttle")
    if args.throttle and not args.once:
        parser.error("--throttle has no effect without --once")

    if args.find:
        for qid, label, description in search_entities(args.find):
            print(f"{qid}\t{label} — {description}")
    elif args.preview:
        preview()
    elif args.star:
        starred = stars.star(output=args.star)
        print("starred" if starred else "unstarred")
    elif args.once or args.output:
        run(throttle=args.throttle, min_interval=args.min_interval, only=args.output)
    else:
        # Imported here: it needs PyGObject + gtk-layer-shell, which no one-shot
        # action does.
        from . import daemon

        daemon.main(args.serve_stars, args.publish_stars)


if __name__ == "__main__":
    main()
