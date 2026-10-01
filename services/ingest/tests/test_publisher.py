import asyncio

import pytest

from ingest.app import create_app
from ingest.publisher import KafkaPublisher, PublishError
from ingest.settings import Settings


def test_app_with_kafka_configured_can_be_created_outside_event_loop() -> None:
    # uvicorn imports the module (and builds the app) before any event loop runs.
    app = create_app(Settings(kafka_bootstrap="localhost:1"))
    assert isinstance(app.state.publisher, KafkaPublisher)


def test_publish_before_start_fails_cleanly() -> None:
    with pytest.raises(PublishError, match="not started"):
        asyncio.run(KafkaPublisher("localhost:1", "t").publish([]))
