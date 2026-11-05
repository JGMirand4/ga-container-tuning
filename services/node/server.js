const cluster = require('cluster');
const http = require('http');

const W = parseInt(process.env.WORKERS || '1', 10);
const fib = (n) => (n < 2 ? n : fib(n - 1) + fib(n - 2));

if (cluster.isPrimary && W > 1) {
  for (let i = 0; i < W; i++) cluster.fork();
} else {
  http
    .createServer((req, res) => {
      if (req.url.startsWith('/cpu')) {
        res.end(String(fib(25)));
      } else if (req.url.startsWith('/io')) {
        setTimeout(() => res.end('ok'), 20);
      } else {
        res.end('ok');
      }
    })
    .listen(8080);
}
