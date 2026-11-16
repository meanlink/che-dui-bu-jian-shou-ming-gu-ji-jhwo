from enum import Enum
class Role(str, Enum):
    admin = 'admin'
    analyst = 'analyst'
    viewer = 'viewer'
class JobStatus(str, Enum):
    queued = 'queued'
    running = 'running'
    completed = 'completed'
    failed = 'failed'
class IntakeStatus(str, Enum):
    open = 'open'
    settled = 'settled'
class IntakeRowStatus(str, Enum):
    accepted = 'accepted'
    quarantined = 'quarantined'
