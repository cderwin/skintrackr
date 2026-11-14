"""The `skintrackr` command."""

import click

from skintrackr.cli.server import serve
from skintrackr.cli.strava_debug import strava_debug


@click.group()
def main() -> None:
    """skintrackr: Strava webhook server and tools."""


main.add_command(serve)
main.add_command(strava_debug)


if __name__ == "__main__":
    main()
