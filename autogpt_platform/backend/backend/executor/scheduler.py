import logging
import time
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from autogpt_libs.utils.cache import thread_cached

from backend.data.block import BlockInput
from backend.data.schedule import (
    ExecutionSchedule,
    add_schedule,
    get_active_schedules,
    get_schedules,
    update_schedule,
)
from backend.executor.manager import ExecutionManager
from backend.util.service import AppService, expose, get_service_client
from backend.util.settings import Config

logger = logging.getLogger(__name__)


def log(msg, **kwargs):
    logger.warning("[ExecutionScheduler] " + msg, **kwargs)


class ExecutionScheduler(AppService):

    def __init__(self, refresh_interval=10):
        super().__init__()
        self.use_db = True
        self.last_check = datetime.min
        self.refresh_interval = refresh_interval

    @classmethod
    def get_port(cls) -> int:
        return Config().execution_scheduler_port

    @property
    @thread_cached
    def execution_client(self) -> ExecutionManager:
        return get_service_client(ExecutionManager)

    def run_service(self):
        scheduler = BackgroundScheduler()
        scheduler.start()
        while True:
            self.__refresh_jobs_from_db(scheduler)
            time.sleep(self.refresh_interval)

    def __refresh_jobs_from_db(self, scheduler: BackgroundScheduler):
        schedules = self.run_and_wait(get_active_schedules(self.last_check))
        for schedule in schedules:
            if schedule.last_updated:
                self.last_check = max(self.last_check, schedule.last_updated)

            if not schedule.is_enabled:
                log(f"Removing recurring job {schedule.id}: {schedule.schedule}")
                scheduler.remove_job(schedule.id)
                continue

            log(f"Adding recurring job {schedule.id}: {schedule.schedule}")
            scheduler.add_job(
                self.__execute_graph,
                CronTrigger.from_crontab(schedule.schedule),
                id=schedule.id,
                args=[schedule.graph_id, schedule.input_data, schedule.user_id],
                replace_existing=True,
            )

    def __execute_graph(self, graph_id: str, input_data: dict, user_id: str):
        try:
            log(f"Executing recurring job for graph #{graph_id}")
            self.execution_client.add_execution(graph_id, input_data, user_id)
        except Exception as e:
            logger.exception(f"Error executing graph {graph_id}: {e}")

    @expose
    def update_schedule(self, schedule_id: str, is_enabled: bool, user_id: str) -> str:
        self.run_and_wait(update_schedule(schedule_id, is_enabled, user_id))
        return schedule_id

    @expose
    def add_execution_schedule(
        self,
        graph_id: str,
        graph_version: int,
        cron: str,
        input_data: BlockInput,
        user_id: str,
    ) -> str:
        schedule = ExecutionSchedule(
            graph_id=graph_id,
            user_id=user_id,
            graph_version=graph_version,
            schedule=cron,
            input_data=input_data,
        )
        return self.run_and_wait(add_schedule(schedule)).id

    @expose
    def get_execution_schedules(self, graph_id: str, user_id: str) -> dict[str, str]:
        schedules = self.run_and_wait(get_schedules(graph_id, user_id=user_id))
        return {v.id: v.schedule for v in schedules}
