from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"


class ContainerGatewaySupervisionTests(unittest.TestCase):
    def test_default_command_leaves_gateway_to_s6_reconciler(self) -> None:
        """A persistent running state must start exactly one gateway.

        Hermes' cont-init reconciler already creates and starts the default
        s6 gateway service.  Docker's main command must therefore only keep
        the container alive; a second `gateway run` races that service and
        makes Coolify mark the deployment unhealthy.
        """
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")

        self.assertIn("HERMES_GATEWAY_NO_SUPERVISE=true", dockerfile)
        self.assertIn('CMD ["gateway", "run", "--no-supervise", "-v"]', dockerfile)
        self.assertIn("patch_hermes_container_boot.py", dockerfile)
