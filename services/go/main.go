package main

import (
	"fmt"
	"net/http"
	"time"
)

func fib(n int) int {
	if n < 2 {
		return n
	}
	return fib(n-1) + fib(n-2)
}

func main() {
	http.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, "ok") })
	http.HandleFunc("/cpu", func(w http.ResponseWriter, r *http.Request) { fmt.Fprintf(w, "%d", fib(25)) })
	http.HandleFunc("/io", func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(20 * time.Millisecond)
		fmt.Fprint(w, "ok")
	})
	http.ListenAndServe(":8080", nil)
}
