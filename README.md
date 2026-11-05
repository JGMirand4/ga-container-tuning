# Ajuste de contêineres com algoritmo genético

**Pergunta de pesquisa:** quanto um algoritmo genético (NSGA-II) melhora o desempenho de um mesmo serviço em
diferentes linguagens (Go, Java, Python, Node) ao ajustar os recursos do contêiner, em comparação com a
configuração padrão, uma configuração manual "razoável" e a busca aleatória com o mesmo orçamento de avaliações?

**Hipóteses**
- **H1:** o desempenho (latência p95/p99 e vazão) varia significativamente entre linguagens, mesmo com configuração padrão.
- **H2:** a configuração otimizada pelo GA supera a padrão e a busca aleatória, com o mesmo orçamento de avaliações.
- **H3:** o ganho do GA depende da linguagem e do tipo de carga (linguagens com JIT ou GC se beneficiam mais do ajuste de memória e threads).

## Estrutura
- `services/*`: API idêntica nas 4 linguagens, com `/cpu` (fib(25) recursivo), `/io` (espera simulada de 20 ms) e `/health`.
  Todas usam servidores de produção: `net/http` (Go), Undertow (Java), gunicorn gthread (Python) e `http`/`cluster` (Node).
- `loadtest/k6.js`: `MODE=rate` (modelo aberto, taxa de chegada fixa, mede p95/p99) ou `MODE=sat` (modelo fechado, mede a vazão máxima).
- `ga/`: `config.py` (genes e limites), `runner.py` (Docker + k6), `nsga2.py` e `run_experiment.py` (orquestração).
- `analysis/analyze.py`: Kruskal-Wallis (H1), hipervolume + Mann-Whitney + delta de Cliff (H2), ganho do GA por linguagem (H3) e curvas de convergência.
- `results/`: `rates.json` (taxa de chegada por linguagem/endpoint) e CSVs das baselines.

## Como executar
Requer host Linux com 8 núcleos e Docker. O gerador de carga fica nos núcleos 4-7 e os serviços nos núcleos 0-3.

```
cd ga
python run_experiment.py setup            # constrói as imagens e a rede
python run_experiment.py calibrate        # taxa de chegada = 30% da saturação da config. padrão
python run_experiment.py baseline         # padrão e manual, 5 repetições cada
python run_experiment.py ga     --seeds 0 1
python run_experiment.py random --seeds 0 1
python ../analysis/analyze.py             # requer pandas, scipy, numpy, matplotlib
```

Teste de fumaça: `python run_experiment.py ga --langs go --endpoints cpu --seeds 0 --pop 4 --gens 2`

Orçamento padrão: população 16 × 10 gerações = 160 avaliações por busca. Use `--pop`, `--gens`, `--langs` e `--endpoints` para alterar.
Cada execução (método, linguagem, endpoint, semente) grava um CSV em `results/` e é pulada se já estiver completa.

## Desenho experimental
- **Cromossomo:** `cpus` (0,25 a 4,0), `mem_mb` (128 MB a 2 GB, escala log), `cpuset` (1 a 4 núcleos contíguos a partir do 0),
  `workers` (1 a 16; vira `GOMAXPROCS`, tamanho do pool do Undertow, workers do gunicorn ou workers do cluster Node)
  e `heap_pct` (25 a 80%, só Java, vira `MaxRAMPercentage`).
- **Reparo:** `cpus <= cpuset`; memória mínima de 256 MB no Java.
- **Fitness (minimizar):** `(p95, -vazão)`. O p95 é a mediana de 3 repetições de 30 s a taxa de chegada fixa, após 20 s de aquecimento.
  A vazão vem de um teste de saturação de 10 s por avaliação.
- **Penalização:** OOM, crash ou mais de 5% de erros marcam o indivíduo como inválido (`ok=False`, excluído das estatísticas).
  Iterações descartadas (chegadas que o serviço não absorveu) inflam o p95 de forma gradual: `p95 × (1 + 10 × fração descartada)`.
- **Baselines:** configuração padrão (sem limites de CPU/memória e sem ajuste), configuração manual (4 CPUs, 1 GB, 4 workers) e busca aleatória.
- **Análise estatística:** Kruskal-Wallis, Mann-Whitney, delta de Cliff, IC por bootstrap e hipervolume da frente de Pareto.

## Decisões e limitações
- A configuração "padrão" não tem limite de CPU/memória, mas fica fixada nos núcleos 0-3 para o gerador de carga nunca competir com o serviço.
- Genomas idênticos dentro de uma mesma execução são medidos uma vez (cache), mas contam no orçamento de 160 avaliações.
- O endpoint de I/O usa espera simulada de 20 ms, sem banco de dados.
- O serviço Java usou inicialmente o `HttpServer` embutido do JDK, que limitava a vazão em ~10× (1.495 contra 15.286 req/s no `/cpu`).
  Foi trocado pelo Undertow para a comparação entre linguagens ser justa.
- Antes do experimento real: fixar as imagens base por digest e colocar o governor de CPU em `performance`.
- O Docker Desktop no macOS roda numa VM. Serve para teste de fumaça, mas não para as medições finais.

## Estado atual
- Pipeline validado de ponta a ponta no Mac (imagens, calibração, baselines, GA com 4 × 2 em cada linguagem no endpoint de CPU).
- Baselines com 5 repetições mostram diferença significativa entre linguagens (H1): p ≈ 0,001 em CPU e em I/O.
- Falta: o experimento real (GA e busca aleatória) no host Linux dedicado e a análise de H2 e H3.
