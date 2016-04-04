import json
from traceback import format_exc
from uuid import uuid1
from time import time
import logging

from tornado import web, websocket, ioloop

rpc_request_map = {}

# rpc_call_map выглядет примерно так:
# rpc_call_map['abcd-xxxx-1111-...'] = {'func': func, 'time': unixtime}
# func вызывается, когда клиент ответил на запрос
# time используется, чтобы удалять старые записи из пула
rpc_call_map = {}


def jsonrpc_method(fn):
    rpc_request_map[fn.__name__] = fn
    return fn


def callback(id):
    """
    Декоратор необходим, чтобы при ответе клиента на запрос сервера
    запустить нужную коллбэк-функцию.

    Декоратор параметром принимает id-запроса, который возвращает функция,
    отправляющая запрос на клиент.

    Когда придёт ответ от клиента, то обработчик по id найдёт в пуле запросов
    функцию, которую нужно вызвать, чтобы обработать ответ клиента.

    Пример использования:
        id = client.test(a, b, c)
        @callback(id)
        def test_response(data, success):
            if success:
                ...
            else:
                ...
    """
    def decorator(fn):

        def wrapped(data):
            return fn(data, is_success(data))

        rpc_call_map[id] = {'func': wrapped, 'time': int(time())}

        return wrapped
    return decorator


def is_success(data):
    """
    Функция проверяет, является ли jsonrpc-ответ успешным.
    """
    try:
        data['result']
        return True
    except KeyError:
        return False


rpc_errors = {
    '-32700': 'Parse error',
    '-32600': 'Invalid Request',
    '-32601': 'Method not found',
    '-32602': 'Invalid params',
    '-32603': 'Internal error',
    '-32000': 'Server error',
}


class ClientCall(object):
    """
    Класс, позволяющий в python-стиле вызвать клиентский jsonrpc-метод.

    Пример:
    id = req.call.some_method('a', 'b')
    хотя some_method в python-коде отсутствует и есть только на стороне js.
    """

    def __init__(self, ws):
        self.ws = ws

    def __getattr__(self, item):
        def func(*args):
            id = str(uuid1())
            self.ws.write_message({'jsonrpc': '2.0',
                                   'method': item,
                                   'params': args,
                                   'id': id})
            return id
        return func


class Request(object):
    def __init__(self, ws, id):
        self.ws = ws
        self.id = id
        self.call = ClientCall(ws)

    def answer(self, data=200):
        self.ws.write_message({'jsonrpc': "2.0",
                               'result': data,
                               'id': self.id})

    def error(self, code, message=None, stack=None):
        if message is None:
            message = rpc_errors[str(code)]
        data = {'code': code, 'message': message}
        if self.ws.application.debug and stack:
            #TODO: поле должно называться stack или data?
            data['stack'] = stack
        self.ws.write_message({'jsonrpc': "2.0",
                               'error': data,
                               'id': self.id})


class WsRpcHandler(websocket.WebSocketHandler):

    def open(self):
        self.call = ClientCall(self)

    def on_message(self, message):
        """
        Точка входа для всех rpc-запросов и ответов.
        """

        logging.info('JSON: %s' % message)
        """
        Разбор json'а и возврат ошибки в случае некорректного json'а.
        """
        try:
            data = json.loads(message)
        except ValueError:
            req = Request(ws=self, id=None)
            req.error(-32700)
            return

        id = data.get('id')

        """
        Определяем является ли сообщение запросом или ответом.
        У запроса обязательно должно быть поле method.
        У ответа - поле result или error.
        """
        method = data.get('method')
        result = data.get('result')
        error = data.get('error')

        if method is None and result is None and error is None:
            """
            Если это не запрос и не ответ, то возвращаем ошибку
            "Метод не найден".
            """
            Request(ws=self, id=id).error(-32601)
            return

        if method:
            req = Request(ws=self, id=id)
            """
            Метод должен быть определён на сервере
            (через декоратор jsonrpc_method).
            """
            if method not in rpc_request_map.keys():
                req.error(-32601)
                return

            params = data.get('params', {})

            """
            В качестве параметра может быть словарь,
            список, число или строка.
            В зависимости от типа параметров
            они по разному передаются в функцию.
            """
            try:
                if type(params) is dict:
                    rpc_request_map[method](req, **params)

                elif type(params) is list:
                    rpc_request_map[method](req, *params)

                elif type(params) in (int, str):
                    rpc_request_map[method](req, params)
            except:
                req.error(-32000, stack=format_exc())
                return

        elif result or error:
            try:
                rpc_call_map[id]['func'](data)
                del rpc_call_map[id]
            except KeyError:
                pass


class RpcListHandler(web.RequestHandler):
    def get(self):
        self.content_type = 'application/json'
        self.write(json.dumps(list(rpc_request_map.keys())))


class JsHandler(web.RequestHandler):
    def get(self):
        self.content_type = 'application/javascript'
        rpc_list = list(rpc_request_map.keys())
        self.render("templates/ws_nado_rpc.js", rpc_list=rpc_list)


def clear_rpc_call_map():
    """
    Удаление старых записей из пула запросов к клиенту.
    Старой считается запись, созданная более пяти минут назад.
    """
    limit = int(time()) - 300
    key_for_del = []
    # поиск старых записей
    for key, value in rpc_call_map.items():
        if value['time'] < limit:
            key_for_del.append(key)
    # удаление
    for key in key_for_del:
        del rpc_call_map[key]

ioloop.PeriodicCallback(clear_rpc_call_map, 5000).start()
