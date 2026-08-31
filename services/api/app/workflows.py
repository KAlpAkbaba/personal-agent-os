"""Minimal M0 Temporal workflow proving durable execution.

HealthPingWorkflow sleeps on a durable timer, then runs an activity. The timer
makes the worker-restart durability test meaningful: the workflow intent lives
in Temporal, not in the worker process.
"""

from datetime import timedelta

from temporalio import activity, workflow

TASK_QUEUE_DEFAULT = "pagentos-core"


@activity.defn
async def ping_activity(message: str) -> str:
    activity.logger.info("ping_activity invoked: %s", message)
    return f"pong:{message}"


@workflow.defn
class HealthPingWorkflow:
    @workflow.run
    async def run(self, message: str, timer_seconds: float = 1.0) -> str:
        # Durable timer: survives worker restarts.
        await workflow.sleep(timedelta(seconds=timer_seconds))
        result: str = await workflow.execute_activity(
            ping_activity,
            message,
            start_to_close_timeout=timedelta(seconds=10),
        )
        return result
