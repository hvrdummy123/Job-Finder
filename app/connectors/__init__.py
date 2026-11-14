from __future__ import annotations

from app.connectors.amazon import AmazonConnector
from app.connectors.ashby import AshbyConnector
from app.connectors.base import Connector, registry
from app.connectors.generic import GenericConnector
from app.connectors.greenhouse import GreenhouseConnector
from app.connectors.lever import LeverConnector
from app.connectors.oracle import OracleConnector
from app.connectors.portals.indeed import IndeedConnector
from app.connectors.portals.naukri import NaukriConnector
from app.connectors.portals.rocketship import RocketshipConnector
from app.connectors.portals.wellfound import WellfoundConnector
from app.connectors.smartrecruiters import SmartRecruitersConnector
from app.connectors.workday import WorkdayConnector

# Registration order matters for detection: portals + specific ATS before generic.
_CONNECTORS: list[Connector] = [
    IndeedConnector(),
    WellfoundConnector(),
    NaukriConnector(),
    RocketshipConnector(),
    AmazonConnector(),
    GreenhouseConnector(),
    LeverConnector(),
    AshbyConnector(),
    WorkdayConnector(),
    SmartRecruitersConnector(),
    OracleConnector(),
    GenericConnector(),
]

for _c in _CONNECTORS:
    registry.register(_c)

__all__ = ["registry", "Connector"]
