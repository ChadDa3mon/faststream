import pytest

from faststream.rabbit import TestApp, TestRabbitBroker


@pytest.mark.asyncio
@pytest.mark.rabbit
async def test_stream():
    from docs.docs_src.rabbit.subscription.stream import app, broker, handle

    async with TestRabbitBroker(broker, with_real=True, connect_only=True):
        async with TestApp(app):
            await handle.wait_call(3)

            handle.mock.assert_called_with("Hi!")
