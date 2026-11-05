import io.undertow.Handlers;
import io.undertow.Undertow;
import io.undertow.server.HttpHandler;
import io.undertow.util.Headers;

public class Main {
    static int fib(int n) { return n < 2 ? n : fib(n - 1) + fib(n - 2); }

    static HttpHandler text(java.util.function.Supplier<String> body) {
        return x -> {
            x.getResponseHeaders().put(Headers.CONTENT_TYPE, "text/plain");
            x.getResponseSender().send(body.get());
        };
    }

    public static void main(String[] args) {
        int workers = Integer.parseInt(System.getenv().getOrDefault("WORKERS", "0"));
        // blocking handlers run on the worker pool (size = gene 'workers'; 0 = Undertow default)
        HttpHandler cpu = Handlers.path()
            .addExactPath("/cpu", new io.undertow.server.handlers.BlockingHandler(text(() -> Integer.toString(fib(25)))))
            .addExactPath("/io", new io.undertow.server.handlers.BlockingHandler(text(() -> {
                try { Thread.sleep(20); } catch (InterruptedException e) { }
                return "ok";
            })))
            .addExactPath("/health", text(() -> "ok"));
        Undertow.Builder b = Undertow.builder().addHttpListener(8080, "0.0.0.0").setHandler(cpu);
        if (workers > 0) b.setWorkerThreads(workers);
        b.build().start();
    }
}
