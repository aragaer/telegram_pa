#!/usr/bin/env python3
import logging
import sys

import asyncio
import os
import signal
import telepot

sys.path.append('.')
asyncio.get_event_loop().set_debug(True)

from assistant.state import StateMachine
from telepot.aio.loop import MessageLoop

_UNIX = "/tmp/pa_socket"


class Client(object):

    def __init__(self, reader, writer, session):
        self._reader = reader
        self._writer = writer
        self._session = session

    async def run_forever(self):
        while True:
            data = await self._reader.readline()
            if not data:
                break
            sdata = data.decode().strip()
            if sdata:
                await self._session.handle_local(sdata, self)

    def write(self, message):
        self._writer.write(message)

    def close(self, task):
        self._writer.close()
        self._session.remove_client(self)


class Server(object):

    _server = None

    def __init__(self, session, path):
        self._session = session
        self._path = path

    async def start(self):
        if os.path.exists(self._path):
            os.unlink(self._path)
        self._server = await asyncio.start_unix_server(self.accept_client, path=self._path)

    def accept_client(self, reader, writer):
        loop = asyncio.get_event_loop()
        client = Client(reader, writer, self._session)
        task = loop.create_task(client.run_forever())
        task.add_done_callback(client.close)

    def stop(self):
        os.unlink(self._path)
        self._server.close()


class Session(object):

    def __init__(self, bot, chat_id, path, can_stop=False):
        self._bot = bot
        self._backends = []
        self._chat_id = chat_id
        self._can_stop = can_stop
        self._state_machine = StateMachine(self)
        self._server = Server(self, path)

    def start(self):
        self._state_machine.handle_event('start')

    def start_server(self):
        loop = asyncio.get_event_loop()
        loop.create_task(self._server.start())

    def stop(self):
        self._state_machine.handle_event('stop')
        self._server.stop()

    def remove_client(self, client):
        if client in self._backends:
            self._backends.remove(client)
            if not self._backends:
                self._state_machine.handle_event('backend gone')

    def send_message(self, message):
        loop = asyncio.get_event_loop()
        loop.create_task(self._bot.sendMessage(self._chat_id, message))

    async def handle_local(self, command, client):
        if command == 'stop' and self._can_stop:
            asyncio.get_event_loop().stop()
        elif command.startswith('message:'):
            message = command[8:].strip()
            self._state_machine.handle_event('response', message)
        elif command == 'register backend':
            self._backends.insert(0, client)
            self._state_machine.handle_event('backend registered')

    def send_to_backend(self, message):
        self._backends[0].write("message:{}\n".format(message).encode())

    async def handle_remote(self, command):
        self._state_machine.handle_event('message', command)

    def start_timer(self, timeout):
        loop = asyncio.get_event_loop()
        self._timer = loop.call_later(timeout, self._state_machine.handle_event, 'done')

    def stop_timer(self):
        if self._timer is not None:
            self._timer.cancel()


class PersonalAssistant(object):
    _bot = None
    _sessions = None
    _friends = None
    _ignored = None

    def __init__(self):
        self._friends = set()
        self._ignored = set()
        owner_id = None
        with open("token.txt") as token_file:
            for line in token_file:
                key, value = line.strip().split()
                if key == 'TOKEN':
                    self._bot = telepot.aio.Bot(value)
                elif key == 'OWNER':
                    owner_id = int(value)
        self._sessions = {
            owner_id: Session(self._bot, owner_id, _UNIX, can_stop=True)
        }

    async def _handle(self, msg):
        content_type, chat_type, chat_id = telepot.glance(msg)
        if chat_id in self._friends and chat_id not in self._sessions:
            session = Session(self._bot, chat_id, _UNIX+str(chat_id))
            self._sessions[chat_id] = session
            session.start()

        if chat_id in self._sessions:
            asyncio.Task(self._sessions[chat_id].handle_remote(msg['text']))
        else:
            if chat_type == 'private':
                if chat_id not in self._ignored:
                    await self._bot.sendMessage(chat_id, "Мы с вами не знакомы")
                    self._ignored.add(chat_id)
            else:
                await self._bot.sendMessage(chat_id, "Я куда-то не туда попалa")
                await self._bot.leaveChat(chat_id)

    def run(self):
        loop = asyncio.get_event_loop()
        for signame in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signame, loop.stop)

        for session in self._sessions.values():
            session.start()
        loop.create_task(MessageLoop(self._bot, self._handle).run_forever())
        loop.run_forever()

        running = asyncio.Task.all_tasks()
        for session in self._sessions.values():
            session.stop()

        pending = [task for task in asyncio.Task.all_tasks() if task not in running]
        print("\n".join(str(task) for task in pending))
        loop.run_until_complete(asyncio.gather(*pending))

        print("Ignored: {}".format(self._ignored))

if __name__ == '__main__':
    logging.basicConfig(stream=sys.stderr, level=logging.DEBUG)
    logging.getLogger('SM').setLevel(logging.DEBUG)
    logging.getLogger('asyncio').setLevel(logging.WARNING)
    logging.getLogger('SM').info("test")
    PersonalAssistant().run()
