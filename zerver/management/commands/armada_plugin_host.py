import signal
import threading
from pathlib import Path
from types import FrameType
from typing import Any

from django.conf import settings
from typing_extensions import override

from zerver.lib.armada_plugin_host import ArmadaPluginHost
from zerver.lib.management import ZulipBaseCommand


class Command(ZulipBaseCommand):
    help = """Run the plugin host: start, watch and stop the processes of the
plugins installed on this server (zerver/lib/armada_plugin_host.py).

Run under supervisor in production (puppet/armada/manifests/plugin_host.pp),
and by tools/run-dev in development. It stops its plugins when it stops.

Usage: ./manage.py armada_plugin_host
"""

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        stop = threading.Event()

        def request_stop(signum: int, frame: FrameType | None) -> None:
            stop.set()

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        plugin_dir = Path(settings.ARMADA_PLUGIN_DIR)
        plugin_dir.mkdir(parents=True, exist_ok=True)
        ArmadaPluginHost(plugin_dir=plugin_dir).run_forever(stop)
