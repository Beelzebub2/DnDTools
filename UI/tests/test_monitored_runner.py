from src.market_lister_job import MonitoredRunner


class _Monitor:
    def __init__(self):
        self.events = []

    def start(self):
        self.events.append("start")

    def stop(self):
        self.events.append("stop")


class _Runner:
    page_size = 10

    def crawl_market(self, pages):
        return pages * 2

    def collect_payouts(self):
        raise RuntimeError("game closed")


def test_actions_run_inside_the_safety_monitor():
    monitor = _Monitor()
    assert MonitoredRunner(_Runner(), monitor).crawl_market(3) == 6
    assert monitor.events == ["start", "stop"]


def test_monitor_stops_even_when_an_action_fails():
    monitor = _Monitor()
    try:
        MonitoredRunner(_Runner(), monitor).collect_payouts()
    except RuntimeError:
        pass
    assert monitor.events == ["start", "stop"]


def test_plain_attributes_pass_through_unwrapped():
    monitor = _Monitor()
    assert MonitoredRunner(_Runner(), monitor).page_size == 10
    assert monitor.events == []
