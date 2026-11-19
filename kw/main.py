"""kw root command group. Thin client: global options, then command groups."""
from __future__ import annotations
import click

from kw import __version__


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--url", default=None, help="API base URL (or KW_URL, or config).")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON output.")
@click.option("-v", "--verbose", count=True, help="Increase log verbosity.")
@click.version_option(__version__, prog_name="kw")
@click.pass_context
def cli(ctx, url, as_json, verbose):
    """KuberWeaver CLI: one door to the API, same roles and pins as the GUI."""
    ctx.obj = {"url": url, "json": as_json, "verbose": verbose}


def _register():
    from kw.commands import auth, connectors, keys
    cli.add_command(auth.auth)
    cli.add_command(connectors.connectors)
    cli.add_command(keys.keys)


_register()

if __name__ == "__main__":
    cli()
