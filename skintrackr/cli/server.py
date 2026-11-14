"""`skintrackr serve`: run the HTTP server."""

import click
import uvicorn


@click.command()
@click.option("--host", default="0.0.0.0", show_default=True, help="Interface to listen on.")
@click.option("--port", default=8080, show_default=True, type=int, help="Port to listen on.")
def serve(host: str, port: int) -> None:
    """Run the webhook and API server. Settings come from the environment (see the README)."""
    # imported here so other commands don't pay for loading the server
    from skintrackr.server.app import create_app

    # log_config=None keeps uvicorn from replacing our logging setup
    uvicorn.run(create_app(), host=host, port=port, log_config=None)
