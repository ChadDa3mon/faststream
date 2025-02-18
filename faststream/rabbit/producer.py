from contextlib import asynccontextmanager
from types import TracebackType
from typing import (
    Any,
    AsyncContextManager,
    AsyncIterator,
    Optional,
    Type,
    Union,
)

import aio_pika
import aiormq
import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

from faststream.broker.parsers import resolve_custom_func
from faststream.broker.types import (
    AsyncCustomDecoder,
    AsyncCustomParser,
    AsyncDecoder,
    AsyncParser,
)
from faststream.exceptions import WRONG_PUBLISH_ARGS
from faststream.rabbit.helpers import RabbitDeclarer
from faststream.rabbit.message import RabbitMessage
from faststream.rabbit.parser import AioPikaParser
from faststream.rabbit.shared.constants import RABBIT_REPLY
from faststream.rabbit.shared.schemas import RabbitExchange, RabbitQueue
from faststream.rabbit.shared.types import TimeoutType
from faststream.rabbit.types import AioPikaSendableMessage
from faststream.types import SendableMessage
from faststream.utils.functions import timeout_scope


class AioPikaFastProducer:
    """A class for fast producing messages using aio-pika.

    Attributes:
        _channel : aio_pika.RobustChannel
            The channel used for publishing messages.
        _rpc_lock : anyio.Lock
            Lock used for RPC calls.
        _decoder : AsyncDecoder
            Decoder used for decoding incoming messages.
        _parser : AsyncParser[aio_pika.IncomingMessage]
            Parser used for parsing incoming messages.
        declarer : RabbitDeclarer
            The declarer object used for declaring exchanges and queues.

    Methods:
        __init__(channel, declarer, parser, decoder): Initializes the AioPikaFastProducer object.
        publish(message, queue, exchange, routing_key, mandatory, immediate, timeout, rpc, rpc_timeout, raise_timeout, persist, reply_to, **message_kwargs): Publishes a message to a queue or exchange.
        _publish(message, exchange, routing_key, mandatory, immediate, timeout, persist, reply_to, **message_kwargs): Publishes a message to an exchange.

    Note: This docstring is incomplete as it is difficult to understand the purpose and functionality of the class and its methods without more context.
    !!! note

        The above docstring is autogenerated by docstring-gen library (https://docstring-gen.airt.ai)
    """

    _channel: aio_pika.RobustChannel
    _rpc_lock: anyio.Lock
    _decoder: AsyncDecoder[Any]
    _parser: AsyncParser[aio_pika.IncomingMessage, Any]
    declarer: RabbitDeclarer

    def __init__(
        self,
        channel: aio_pika.RobustChannel,
        declarer: RabbitDeclarer,
        parser: Optional[AsyncCustomParser[aio_pika.IncomingMessage, RabbitMessage]],
        decoder: Optional[AsyncCustomDecoder[RabbitMessage]],
    ):
        """Initialize a class instance.

        Args:
            channel: The aio_pika.RobustChannel object.
            declarer: The RabbitDeclarer object.
            parser: An optional AsyncCustomParser object for parsing incoming messages.
            decoder: An optional AsyncCustomDecoder object for decoding incoming messages.
        !!! note

            The above docstring is autogenerated by docstring-gen library (https://docstring-gen.airt.ai)
        """
        self._channel = channel
        self.declarer = declarer
        self._parser = resolve_custom_func(parser, AioPikaParser.parse_message)
        self._decoder = resolve_custom_func(decoder, AioPikaParser.decode_message)
        self._rpc_lock = anyio.Lock()

    async def publish(
        self,
        message: AioPikaSendableMessage = "",
        queue: Union[RabbitQueue, str] = "",
        exchange: Union[RabbitExchange, str, None] = None,
        *,
        routing_key: str = "",
        mandatory: bool = True,
        immediate: bool = False,
        timeout: TimeoutType = None,
        rpc: bool = False,
        rpc_timeout: Optional[float] = 30.0,
        raise_timeout: bool = False,
        persist: bool = False,
        reply_to: Optional[str] = None,
        **message_kwargs: Any,
    ) -> Union[aiormq.abc.ConfirmationFrameType, SendableMessage]:
        """Publish a message to a RabbitMQ queue.

        Args:
            message (AioPikaSendableMessage): The message to be published.
            queue (Union[RabbitQueue, str]): The queue to publish the message to.
            exchange (Union[RabbitExchange, str, None]): The exchange to publish the message to.
            routing_key (str): The routing key for the message.
            mandatory (bool): Whether the message is mandatory.
            immediate (bool): Whether the message should be delivered immediately.
            timeout (TimeoutType): The timeout for the operation.
            rpc (bool): Whether the message is for RPC.
            rpc_timeout (Optional[float]): The timeout for RPC.
            raise_timeout (bool): Whether to raise an exception on timeout.
            persist (bool): Whether the message should be persisted.
            reply_to (Optional[str]): The reply-to queue for RPC.
            **message_kwargs (Any): Additional keyword arguments for the message.

        Returns:
            Union[aiormq.abc.ConfirmationFrameType, SendableMessage]: The result of the publish operation.

        Raises:
            WRONG_PUBLISH_ARGS: If reply_to is not None when rpc is True.
        !!! note

            The above docstring is autogenerated by docstring-gen library (https://docstring-gen.airt.ai)
        """
        p_queue = RabbitQueue.validate(queue)

        context: AsyncContextManager[
            Optional[MemoryObjectReceiveStream[aio_pika.IncomingMessage]]
        ]
        if rpc is True:
            if reply_to is not None:
                raise WRONG_PUBLISH_ARGS
            else:
                context = _RPCCallback(
                    self._rpc_lock,
                    self.declarer.queues[RABBIT_REPLY],
                )
        else:
            context = _fake_context()

        async with context as response_queue:
            r = await self._publish(
                message=message,
                exchange=exchange,
                routing_key=routing_key or p_queue.routing or "",
                mandatory=mandatory,
                immediate=immediate,
                timeout=timeout,
                persist=persist,
                reply_to=RABBIT_REPLY if response_queue else reply_to,
                **message_kwargs,
            )

            if response_queue is None:
                return r

            else:
                msg: Optional[aio_pika.IncomingMessage] = None
                with timeout_scope(rpc_timeout, raise_timeout):
                    msg = await response_queue.receive()

                if msg:
                    return await self._decoder(await self._parser(msg))

        return None

    async def _publish(
        self,
        message: AioPikaSendableMessage = "",
        exchange: Union[RabbitExchange, str, None] = None,
        *,
        routing_key: str = "",
        mandatory: bool = True,
        immediate: bool = False,
        timeout: TimeoutType = None,
        persist: bool = False,
        reply_to: Optional[str] = None,
        **message_kwargs: Any,
    ) -> Union[aiormq.abc.ConfirmationFrameType, SendableMessage]:
        """Publish a message to a RabbitMQ exchange.

        Args:
            message (AioPikaSendableMessage): The message to be published.
            exchange (Union[RabbitExchange, str, None]): The exchange to publish the message to.
            routing_key (str): The routing key for the message.
            mandatory (bool): Whether the message is mandatory.
            immediate (bool): Whether the message should be delivered immediately.
            timeout (TimeoutType): The timeout for the operation.
            persist (bool): Whether the message should be persisted.
            reply_to (Optional[str]): The reply-to address for the message.
            **message_kwargs (Any): Additional keyword arguments for the message.

        Returns:
            Union[aiormq.abc.ConfirmationFrameType, SendableMessage]: The result of the publish operation.

        Raises:
            NotImplementedError: If silent animals are not supported.
        !!! note

            The above docstring is autogenerated by docstring-gen library (https://docstring-gen.airt.ai)
        """
        p_exchange = RabbitExchange.validate(exchange)

        if p_exchange is None:
            exchange_obj = self._channel.default_exchange
        else:
            p_exchange.passive = True
            exchange_obj = await self.declarer.declare_exchange(p_exchange)

        message = AioPikaParser.encode_message(
            message=message,
            persist=persist,
            reply_to=reply_to,
            **message_kwargs,
        )

        return await exchange_obj.publish(
            message=message,
            routing_key=routing_key,
            mandatory=mandatory,
            immediate=immediate,
            timeout=timeout,
        )


class _RPCCallback:
    """A class representing an RPC callback.

    Attributes:
        lock : a lock object used for synchronization
        queue : a robust queue object for receiving callback messages

    Methods:
        __aenter__ : Asynchronous context manager method that acquires the lock and returns a memory object receive stream for incoming messages.
        __aexit__ : Asynchronous context manager method that releases the lock and cancels the consumer tag for the queue.

    Returns:
        None
    !!! note

        The above docstring is autogenerated by docstring-gen library (https://docstring-gen.airt.ai)
    """

    def __init__(self, lock: anyio.Lock, callback_queue: aio_pika.RobustQueue):
        """Initialize an object of a class.

        Args:
            lock: An instance of `anyio.Lock` used for synchronization.
            callback_queue: An instance of `aio_pika.RobustQueue` used for callback queue.
        !!! note

            The above docstring is autogenerated by docstring-gen library (https://docstring-gen.airt.ai)
        """
        self.lock = lock
        self.queue = callback_queue

    async def __aenter__(self) -> MemoryObjectReceiveStream[aio_pika.IncomingMessage]:
        send_response_stream: MemoryObjectSendStream[Any]
        receive_response_stream: MemoryObjectReceiveStream[aio_pika.IncomingMessage]
        (
            send_response_stream,
            receive_response_stream,
        ) = anyio.create_memory_object_stream(max_buffer_size=1)
        await self.lock.acquire()

        self.consumer_tag = await self.queue.consume(
            callback=send_response_stream.send,
            no_ack=True,
        )

        return receive_response_stream

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]] = None,
        exc_val: Optional[BaseException] = None,
        exec_tb: Optional[TracebackType] = None,
    ) -> None:
        """Exit method for an asynchronous context manager.

        Args:
            exc_type: The type of the exception being handled, if any.
            exc_val: The exception instance being handled, if any.
            exec_tb: The traceback of the exception being handled, if any.

        Returns:
            None
        !!! note

            The above docstring is autogenerated by docstring-gen library (https://docstring-gen.airt.ai)
        """
        self.lock.release()
        await self.queue.cancel(self.consumer_tag)


@asynccontextmanager
async def _fake_context() -> AsyncIterator[None]:
    yield None
