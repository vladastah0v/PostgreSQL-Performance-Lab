import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import gevent
from gevent.event import Event
from locust import HttpUser, constant, task
from locust.env import Environment

from benchmarks.scenarios.analytics import LoadConfig, select_scenarios


class Trial:
    def __init__(self, config: LoadConfig):
        self.config = config
        self.scenarios = select_scenarios(config.scenario)
        self.done = Event()
        self.idle = Event()
        self.idle.set()
        self.phase = "warmup"
        self.in_flight = 0
        self.warmup_completed = 0
        self.warmup_failed = 0
        self.issued = Counter()
        self.samples = []
        self.user_index = 0
        self.last_response_at = 0.0

    def reserve(self, operation: str) -> bool | None:
        if self.phase == "measuring":
            if self.issued.total() >= self.config.max_requests:
                return None
            self.issued[operation] += 1
        elif self.phase != "warmup":
            return None
        self.in_flight += 1
        self.idle.clear()
        return self.phase == "measuring"

    def finish_request(self) -> None:
        self.in_flight -= 1
        if self.in_flight == 0:
            self.idle.set()

    def record(
        self,
        name,
        response_time,
        response_length,
        response,
        context,
        exception,
        **kwargs,
    ) -> None:
        if not context.get("measured"):
            self.warmup_completed += 1
            self.warmup_failed += int(exception is not None)
            return
        self.last_response_at = perf_counter()
        self.samples.append(
            {
                "operation": name,
                "latency_ms": round(response_time, 3),
                "response_bytes": response_length,
                "status": response.status_code if response is not None else None,
                "error": str(exception) if exception is not None else None,
            }
        )
        if len(self.samples) >= self.config.max_requests:
            self.done.set()


class LabUser(HttpUser):
    wait_time = constant(0.1)

    def on_start(self) -> None:
        self.trial = self.environment.trial
        self.client.trust_env = False
        self.initial_step = self.trial.user_index
        self.step = self.initial_step
        self.measurement_started = False
        self.trial.user_index += 1

    @task
    def request_scenario(self) -> None:
        if self.trial.phase == "measuring" and not self.measurement_started:
            self.step = self.initial_step
            self.measurement_started = True
        scenario = self.trial.scenarios[self.step % len(self.trial.scenarios)]
        self.step += 1
        measured = self.trial.reserve(scenario.name)
        if measured is None:
            return

        config = self.trial.config
        variant = config.variant
        try:
            with self.client.get(
                scenario.url(variant),
                name=scenario.name,
                params=scenario.parameters,
                timeout=(2, config.request_timeout),
                allow_redirects=False,
                context={"measured": measured},
                catch_response=True,
            ) as response:
                if response.status_code == 0:
                    response.failure(type(response.error).__name__)
                elif response.status_code != 200:
                    response.failure(f"HTTP {response.status_code}")
                elif (
                    expected := scenario.cache_status(variant)
                ) is not None and response.headers.get("X-Cache-Status") != expected:
                    response.failure("Unexpected analytics cache status")
        finally:
            self.trial.finish_request()


def run(config: LoadConfig) -> dict:
    trial = Trial(config)
    environment = Environment(
        user_classes=[LabUser],
        host=config.host,
        stop_timeout=config.request_timeout + 2,
    )
    environment.trial = trial
    runner = environment.create_local_runner()
    spawned = Event()

    def spawning_complete(**kwargs) -> None:
        spawned.set()

    environment.events.spawning_complete.add_listener(spawning_complete)
    environment.events.request.add_listener(trial.record)
    try:
        runner.start(config.users, spawn_rate=config.spawn_rate)
        if not spawned.wait(timeout=config.users / config.spawn_rate + 10):
            raise RuntimeError("Locust did not spawn all users")
        gevent.sleep(config.warmup)
        trial.phase = "pausing"
        if not trial.idle.wait(timeout=config.request_timeout + 4):
            raise RuntimeError("Warmup requests did not finish")
        started_at = datetime.now(UTC)
        started = perf_counter()
        trial.phase = "measuring"
        reached_budget = trial.done.wait(timeout=config.duration)
        trial.phase = "draining"
        stop_issuing = perf_counter()
    finally:
        runner.quit()

    elapsed = max(stop_issuing, trial.last_response_at) - started
    return {
        "started_at_utc": started_at.isoformat(),
        "elapsed_seconds": elapsed,
        "issuing_seconds": stop_issuing - started,
        "stop_reason": "max_requests" if reached_budget else "duration",
        "issued": dict(trial.issued),
        "warmup_completed": trial.warmup_completed,
        "warmup_failed": trial.warmup_failed,
        "samples": trial.samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one local Locust trial from a benchmark configuration."
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = LoadConfig(**json.loads(args.config.read_text(encoding="utf-8")))
    report = run(config)
    with args.output.open("x", encoding="utf-8") as file:
        json.dump(report, file, separators=(",", ":"))
        file.write("\n")


if __name__ == "__main__":
    main()
