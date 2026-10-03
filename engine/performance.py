"""Bounded runtime measurements and isolated dashboard delivery."""
import asyncio
import math
import time
from collections import defaultdict, deque


class PerformanceMetrics:
    def __init__(self, window=512):
        self.samples = defaultdict(lambda: deque(maxlen=window))
        self.counters = defaultdict(int)

    def observe(self, stage, started):
        self.record(stage, (time.perf_counter() - started) * 1000)

    def record(self, stage, milliseconds):
        if math.isfinite(milliseconds) and milliseconds >= 0:
            self.samples[stage].append(milliseconds)

    def snapshot(self):
        result = {}
        for stage, values in self.samples.items():
            ordered = sorted(values)
            result[stage] = {
                "samples": len(ordered),
                "mean_ms": round(sum(ordered) / len(ordered), 3),
                "p95_ms": round(ordered[math.ceil(len(ordered) * .95) - 1], 3),
                "p99_ms": round(ordered[math.ceil(len(ordered) * .99) - 1], 3),
            }
        return {"stages": result, "counters": dict(self.counters)}


class DashboardSender:
    """One writer per socket; coalesce heartbeat state, bound event backlog."""
    def __init__(self, socket, authorized, disconnected, metrics, capacity=32):
        self.socket = socket
        self.authorized = authorized
        self.disconnected = disconnected
        self.metrics = metrics
        self.capacity = capacity
        self.events = deque()
        self.state = None
        self.overloaded = False
        self.cleaned = False
        self.last_history = None
        self.ready = asyncio.Event()
        self.task = asyncio.create_task(self.run())

    def enqueue(self, message):
        if message.get("type") == "HEARTBEAT":
            # Preserve history updates when a newer heartbeat omits them.
            self.state = {**(self.state or {}), **message}
        elif len(self.events) < self.capacity:
            self.events.append(message)
            if self.state is not None:
                self.state.update({key: value for key, value in message.items()
                                   if key != "type" and key in self.state})
        else:
            self.metrics.counters["dashboard_overflow"] += 1
            self.overloaded = True
        self.ready.set()

    async def run(self):
        close_code = 1000
        try:
            while True:
                await self.ready.wait()
                if not self.authorized():
                    close_code = 4401
                    break
                if self.overloaded:
                    close_code = 1013
                    break
                if self.events:
                    message = self.events.popleft()
                else:
                    message, self.state = self.state, None
                if not self.events and self.state is None:
                    self.ready.clear()
                history = message.get("closed_positions")
                if history is not None and self.last_history is not None and message.get("type") != "INITIAL_SNAPSHOT":
                    message = dict(message)
                    message.pop("closed_positions")
                    if history != self.last_history:
                        previous = {row["position_id"]: row for row in self.last_history}
                        message["closed_positions_delta"] = {
                            "upserts": [row for row in history if previous.get(row["position_id"]) != row],
                            "order": [row["position_id"] for row in history],
                        }
                started = time.perf_counter()
                if message.get("type") == "PONG":
                    await asyncio.wait_for(self.socket.send_text("pong"), .8)
                else:
                    await asyncio.wait_for(self.socket.send_json(message), .8)
                if history is not None:
                    self.last_history = history
                self.metrics.observe("dashboard_send", started)
        except asyncio.CancelledError:
            pass
        except Exception:
            close_code = 1013
            self.metrics.counters["dashboard_disconnect"] += 1
        finally:
            await self.cleanup(close_code)

    async def cleanup(self, code=1000):
        if self.cleaned:
            return
        self.cleaned = True
        self.disconnected()
        self.events.clear()
        self.state = None
        try:
            await asyncio.wait_for(self.socket.close(code=code), .8)
        except Exception:
            pass

    async def stop(self):
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass
        # A task cancelled before its first turn does not execute its finally.
        await self.cleanup()
