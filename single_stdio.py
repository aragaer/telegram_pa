#!/usr/bin/env python3
import asyncio
import json
import sys

from asyncio.streams import FlowControlMixin
from telepot import Bot
from telepot.exception import TelepotException


async def stdin_stream_reader(loop):
    reader = asyncio.StreamReader()
    reader_protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: reader_protocol, sys.stdin)
    return reader


async def stdout_stream_writer(loop):
    writer_props = await loop.connect_write_pipe(FlowControlMixin, sys.stdout)
    return asyncio.StreamWriter(*writer_props, None, loop)


class Tg2Stdio(object):

    def __init__(self, token):
        self._token = token
        self._bot = Bot(token)
        self._reader = self._writer = None

    async def run(self):
        loop = asyncio.get_event_loop()
        self._reader = await stdin_stream_reader(loop)
        self._writer = await stdout_stream_writer(loop)
        await asyncio.gather(self.reader_task(), self.bot_task())

    async def bot_task(self):
        offset = None
        while True:
            updates = []
            try:
                updates = self._bot.getUpdates(offset=offset, timeout=0.8)
            except TelepotException:
                # happens when computer goes to sleep
                self._bot = Bot(token)
            for message in updates:
                data = json.dumps(message, ensure_ascii=False)
                self._writer.write(data.encode())
                self._writer.write(b'\n')
                await self._writer.drain()
                offset = message['update_id'] + 1
            await asyncio.sleep(0.2)

    async def reader_task(self):
        while not self._reader.at_eof():
            data = await self._reader.readline()
            if not data:
                break
            await self.handle_local_message(data.decode())
        asyncio.get_event_loop().stop()

    async def handle_local_message(self, data):
        message = json.loads(data)
        if 'text' in message:
            if isinstance(message['text'], list):
                for line in message['text']:
                    if line:
                        self._bot.sendMessage(message['chat_id'], line)
            elif message['text']:
                self._bot.sendMessage(message['chat_id'], message['text'])


if __name__ == '__main__':
    import argparse, signal
    parser = argparse.ArgumentParser(description="Telegram-to-stdio")
    parser.add_argument("--token-file", default="token.txt", help="Telegram token file")
    parser.add_argument("--token", help="Telegram token")
    args = parser.parse_args()
    if args.token is None:
        with open(args.token_file) as token_file:
            for line in token_file:
                key, value = line.split()
                if key == "TOKEN":
                    token = value
    else:
        token = args.token
    loop = asyncio.get_event_loop()
    for signame in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signame, loop.stop)
    loop.create_task(Tg2Stdio(token).run())
    loop.run_forever()
