import time


def fib(n):
    return n if n < 2 else fib(n - 1) + fib(n - 2)


def app(environ, start_response):
    path = environ["PATH_INFO"]
    if path == "/cpu":
        body = str(fib(25))
    elif path == "/io":
        time.sleep(0.02)
        body = "ok"
    else:
        body = "ok"
    data = body.encode()
    start_response("200 OK", [("Content-Length", str(len(data)))])
    return [data]
