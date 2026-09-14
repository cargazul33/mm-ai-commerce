from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from mm_commerce.models import AgentFinding, AgentRun


class BaseAgent:
    name = "base"

    def __init__(self, session: Session):
        self.session = session

    def start_run(self, opportunity_id: int | None = None) -> AgentRun:
        run = AgentRun(
            agent_name=self.name,
            opportunity_id=opportunity_id,
            status="RUNNING",
            started_at=datetime.now(timezone.utc),
        )
        self.session.add(run)
        self.session.flush()
        return run

    def finish_run(self, run: AgentRun, summary: str, status: str = "OK") -> None:
        run.status = status
        run.summary = summary
        run.finished_at = datetime.now(timezone.utc)
        self.session.add(run)

    def finding(
        self,
        run: AgentRun,
        message: str,
        *,
        opportunity_id: int | None = None,
        severity: str = "INFO",
        code: str = "",
        blocks: bool = False,
    ) -> AgentFinding:
        f = AgentFinding(
            agent_run_id=run.id,
            opportunity_id=opportunity_id or run.opportunity_id,
            severity=severity,
            code=code,
            message=message,
            blocks=blocks,
        )
        self.session.add(f)
        return f
