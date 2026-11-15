"""The `skintrackr` command."""

import click

from skintrackr.cli.migrate import migrate
from skintrackr.cli.server import serve


@click.group()
def main() -> None:
    """skintrackr: intervals.icu activity archive and stats."""


main.add_command(serve)
main.add_command(migrate)


if __name__ == "__main__":
    main()
