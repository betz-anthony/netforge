import argparse
import logging
import traceback
from pathlib import Path

LOG_PATH = Path.home() / ".netforge.log"

logging.basicConfig(
    filename=str(LOG_PATH),
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("netforge.main")


def main():
    parser = argparse.ArgumentParser(description="Windows DNS/DHCP TUI Manager")
    parser.add_argument("--host", help="DC hostname or IP")
    parser.add_argument("--user", help="Username (DOMAIN\\user)")
    parser.add_argument("--port", type=int, default=5985)
    parser.add_argument("--no-ssl", action="store_true", help="Use HTTP instead of HTTPS")
    args = parser.parse_args()

    try:
        from netforge.ui.app import NetForgeApp
        app = NetForgeApp(
            cli_host=args.host,
            cli_user=args.user,
            cli_port=args.port,
            cli_ssl=not args.no_ssl,
        )

        # Hook Textual's internal exception handler so widget/render crashes
        # go to our log file rather than disappearing silently
        original_handle_exception = app._handle_exception if hasattr(app, '_handle_exception') else None

        def _on_exception(error: Exception) -> None:
            log.error("Textual internal exception: %s", error)
            log.error(traceback.format_exc())
            if original_handle_exception:
                original_handle_exception(error)

        if hasattr(app, '_handle_exception'):
            app._handle_exception = _on_exception

        app.run()
    except Exception as e:
        log.error("Fatal crash: %s", e)
        log.error(traceback.format_exc())
        # Also print to stderr in case terminal is still available
        print(f"\nFatal error: {e}")
        print(f"Full traceback written to {LOG_PATH}")
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
