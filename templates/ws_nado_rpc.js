"use strict";
var name_list = {% raw rpc_list %};
var server = {};
var pool = {};

function create_uuid() {
    // http://byronsalau.com/blog/how-to-create-a-guid-uuid-in-javascript/
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
        var r = Math.random()*16|0, v = c === 'x' ? r : (r&0x3|0x8);
        return v.toString(16);
    });
}

function rpc_send(name, params, callback) {
    var data = {
        "jsonrpc": "2.0",
        "method": name,
        "id": create_uuid()
    };
    // параметров может не быть
    if(params.length>0) {
        // Если параметр один, то передаём его не как список
        if(params.length==1) {
            data.params = params[0];
        }
        else {
            data.params = params;
        }
    }
    var str = JSON.stringify(data);
    pool[data.id] = {
        "callback": callback,
        "time": new Date().getTime()
    };
    ws.send(str);
}

name_list.forEach(function(name) {
    server[name] = function() {
        var params = [];
        for(var i=0; i<arguments.length; i++) {
            params.push(arguments[i]);
        }
        var callback = {};
        rpc_send(name, params, callback);
        return callback;
    };
});

function ws_onmessage(event) {
    var data = JSON.parse(event.data);
    // Если есть result или error, то это ответ от сервера
    if(data.jsonrpc && data.id && (data.result || data.error)) {
        // Пришёл ответ без ошибки
        if(data.result) {
            if (pool[data.id].callback.success) {
                pool[data.id].callback.success(data.result);
            }
        }
        // Пришёл ответ с ошибкой
        if(data.error) {
            if (pool[data.id].callback.error) {
                pool[data.id].callback.error(data.error);
            }
        }
        // Зевершение обработки ответа
        if (pool[data.id].callback.finally) {
            pool[data.id].callback.finally(data);
        }

        // Удаление записи из пула, чтобы не загромождать его
        delete pool[data.id];
    }

    // Если есть method, то это запрос от сервера
    else if(data.jsonrpc && data.id && data.method) {
        if(!window[data.method]) return;
        var params = [];
        if(data.params) {
            // Т.к. функция apply требует передачу массива,
            // то параметр надо привести к массиву когда он им не является
            if(Array.isArray(data.params)) {
                params = data.params;
            }
            else {
                params = [data.params];
            }
        }
        // Первым параметром функции передаётся id запроса, если id есть
        // а если нет, то для сохранения общего шаблона передаём null
        var id = null;
        if(data.id){
            id = data.id;
        }
        params = [id].concat(params);
        window[data.method].apply(this, params);
    }
}

function req_answer(id, data) {
    var j = {"jsonrpc": "2.0", "result": data, "id": id};
    var str = JSON.stringify(j);
    ws.send(str);
}

function req_answer_error(id, error) {
    var j = {"jsonrpc": "2.0", "error": error, "id": id};
    var str = JSON.stringify(j);
    ws.send(str);
}

setInterval(function() {
    // очистка пула от старых записей
    // старыми считаются те, которые добавлены в пул ранее,
    // чем 5 минут назад (300 000 миллисекунд)
    // очистка запускается каждые 5 секунд
    var limit = new Date().getTime() - 30000;
    for(var key in pool) {
        if(pool[key].time<limit) {
            delete pool[key];
        }
    }
}, 5000);
