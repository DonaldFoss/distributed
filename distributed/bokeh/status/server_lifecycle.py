#!/usr/bin/env python
from __future__ import print_function, division, absolute_import

from collections import deque
import json
import logging
import os
import sys
from time import time

from tornado import gen
from tornado.httpclient import AsyncHTTPClient, HTTPError
from tornado.iostream import StreamClosedError
from tornado.ioloop import IOLoop

from distributed.compatibility import ConnectionRefusedError
from distributed.core import read, connect, write
from distributed.protocol.pickle import dumps
from distributed.diagnostics.progress_stream import progress_stream
from distributed.bokeh.worker_monitor import resource_append
import distributed.bokeh
from distributed.bokeh.utils import parse_args
from distributed.utils import log_errors


logger = logging.getLogger(__name__)

client = AsyncHTTPClient()

messages = distributed.bokeh.messages  # monkey-patching

options = parse_args(sys.argv[1:])


@gen.coroutine
def http_get(route):
    """ Get data from JSON route, store in messages deques """
    try:
        url = 'http://%(host)s:%(http-port)d/' % options + route + '.json'
        response = yield client.fetch(url)
    except ConnectionRefusedError as e:
        logger.info("Can not connect to %s", url, exc_info=True)
        return
    except HTTPError:
        logger.warn("http route %s failed", route)
        return
    msg = json.loads(response.body.decode())
    messages[route]['deque'].append(msg)
    messages[route]['times'].append(time())


last_index = [0]
@gen.coroutine
def workers():
    """ Get data from JSON route, store in messages deques """
    try:
        response = yield client.fetch(
                'http://%(host)s:%(http-port)d/workers.json' % options)
    except HTTPError:
        logger.warn("workers http route failed")
        return
    msg = json.loads(response.body.decode())
    if msg:
        messages['workers']['deque'].append(msg)
        messages['workers']['times'].append(time())
        resource_append(messages['workers']['plot-data'], msg)
        index = messages['workers']['index']
        index.append(last_index[0] + 1)
        last_index[0] += 1


@gen.coroutine
def progress():
    with log_errors():
        stream = yield progress_stream('%(host)s:%(tcp-port)d' % options, 0.050)
        while True:
            try:
                msg = yield read(stream)
            except StreamClosedError:
                break
            else:
                messages['progress'] = msg


@gen.coroutine
def processing():
    with log_errors():
        from distributed.diagnostics.scheduler import processing
        stream = yield connect(ip=options['host'], port=options['tcp-port'])
        yield write(stream, {'op': 'feed',
                             'function': dumps(processing),
                             'interval': 0.200})
        while True:
            try:
                msg = yield read(stream)
            except StreamClosedError:
                break
            else:
                messages['processing'] = msg


def on_server_loaded(server_context):
    server_context.add_periodic_callback(workers, 500)

    server_context.add_periodic_callback(lambda: http_get('tasks'), 100)
    IOLoop.current().add_callback(processing)

    IOLoop.current().add_callback(progress)
