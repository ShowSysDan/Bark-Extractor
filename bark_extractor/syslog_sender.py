"""
Optional syslog integration for Bark Extractor.

Sends job lifecycle events (started, completed, failed, cancelled)
to a remote syslog server over UDP.
"""

import logging
import logging.handlers

_logger = None
_handler = None


def configure(enabled: bool, host: str = "localhost", port: int = 514):
    """Configure (or disable) the syslog handler.  Safe to call at runtime."""
    global _logger, _handler

    # Tear down any existing handler
    if _logger and _handler:
        _logger.removeHandler(_handler)
        try:
            _handler.close()
        except Exception:
            pass
        _handler = None
        _logger = None

    if not enabled:
        return

    try:
        handler = logging.handlers.SysLogHandler(address=(host, int(port)))
        handler.setFormatter(logging.Formatter("bark-extractor: %(message)s"))

        logger = logging.getLogger("bark-extractor.syslog")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.handlers.clear()
        logger.addHandler(handler)

        _handler = handler
        _logger = logger
    except Exception as exc:
        # Bad host/port – log locally and leave syslog disabled
        logging.getLogger(__name__).warning(
            "Could not configure syslog (%s:%s): %s", host, port, exc
        )


def send(msg: str):
    """Send a message to syslog if a handler is configured."""
    if _logger is not None:
        try:
            _logger.info(msg)
        except Exception:
            pass
