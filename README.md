# Ajuste de contêineres com algoritmo genético

**Pergunta de pesquisa:** quanto um algoritmo genético (NSGA-II) melhora o compromisso entre latência, vazão e custo de
recursos de um mesmo serviço em diferentes linguagens (Go, Java, Python, Node), em comparação com a configuração padrão,
uma configuração manual "razoável" e a busca aleatória com o mesmo orçamento de avaliações?

**Hipóteses**
- **H1:** o desempenho (vazão de saturação e latência p95) varia significativamente entre linguagens, mesmo com configuração padrão.
- **H2:** o NSGA-II encontra frentes de Pareto melhores (hipervolume em p95 × vazão × custo) que a busca aleatória, com o mesmo orçamento.
- **H3:** o ganho do ajuste em relação à configuração padrão (redução de p95 e economia de recursos sob o SLO) depende da linguagem
  e do tipo de carga (linguagens com JIT/GC ou processo único se beneficiam mais do ajuste de workers, memória e heap).

## Estrutura
- `services/*`: API idêntica nas 4 linguagens, com `/cpu` (fib(25) recursivo), `/io` (espera simulada de 20 ms) e `/health`.
  Todas usam servidores de produção: `net/http` (Go), Undertow (Java), gunicorn gthread (Python) e `http`/`cluster` (Node).
- `loadtest/k6.js`: `MODE=rate` (modelo aberto, taxa de chegada fixa, mede p95/p99) ou `MODE=sat` (modelo fechado, mede a vazão máxima de respostas bem-sucedidas).
- `ga/`: `config.py` (genes, limites e constantes do experimento), `runner.py` (Docker + k6), `nsga2.py` e `run_experiment.py` (orquestração).
- `analysis/analyze.py`: verificações de qualidade, H1, H2, H3 e curvas de convergência.
- `results/`: dados do experimento real. `results/mac/` guarda as medições de validação feitas no Docker Desktop (não usar como resultado final).

## Como executar
Requer host Linux com 8 núcleos e Docker. O gerador de carga fica nos núcleos 4-7 e os serviços nos núcleos 0-3.

```
cd ga
python run_experiment.py plan                    # estimativa de tempo para os parâmetros escolhidos
python run_experiment.py setup                   # constrói as imagens e a rede
python run_experiment.py calibrate               # taxa = 50% da saturação da config. padrão + taxa comum por endpoint
python run_experiment.py baseline --repeats 10   # padrão, manual e padrão@taxa comum; serve também de teste A/A
python ../analysis/analyze.py                    # confira as verificações de qualidade ANTES de seguir
python run_experiment.py ga                      # sementes 0-4, população 12 × 8 gerações
python run_experiment.py random
python run_experiment.py reeval                  # remede as configs escolhidas de cada busca (5 repetições)
python ../analysis/analyze.py                    # requer pandas, scipy, numpy, matplotlib
```

Teste de fumaça: `python run_experiment.py ga --langs go --endpoints cpu --seeds 0 --pop 4 --gens 2 --results /tmp/smoke`
(copie um `rates.json` para `/tmp/smoke` antes).

Opções: `--langs`, `--endpoints`, `--seeds`, `--pop`, `--gens`, `--repeats`, `--rate-frac`, `--slo-factor`,
`--warmup`, `--duration`, `--reps`, `--sat` e `--results`. Cada CSV é pulado se estiver completo. Um CSV incompleto
é refeito, e um CSV no formato antigo de colunas é renomeado para `.legacy` em vez de apagado.

### Orçamento de tempo
Uma avaliação leva cerca de 85 s (15 s de aquecimento + 2 × 20 s a taxa fixa + 10 s de saturação + overhead do Docker).
Saída de `plan` com os valores padrão (5 sementes, 12 × 8 = 96 avaliações por busca, limite superior sem contar o cache):

| Escopo | Buscas | Tempo total |
|---|---|---|
| 4 linguagens × 2 endpoints | 80 | ~8,5 dias |
| 4 linguagens × só `/cpu` | 40 | ~4,2 dias |

Se o prazo não comportar, rode primeiro `--endpoints cpu` (o caso em que o ajuste de workers e CPU mais importa) e só depois `/io`.
Não reduza para menos de 5 sementes: com 2 contra 2, o menor p-valor possível do Mann-Whitney é cerca de 0,17.

## Desenho experimental
- **Cromossomo:** `cpus` (0,25 a 4,0), `mem_mb` (128 MB a 2 GB, escala log), `cpuset` (1 a 4 núcleos contíguos a partir do 0),
  `workers` (1 a 16; vira `GOMAXPROCS`, tamanho do pool do Undertow, workers do gunicorn ou workers do cluster Node)
  e `heap_pct` (25 a 80%, só Java, vira `MaxRAMPercentage`).
- **Reparo:** `cpus <= cpuset`; memória mínima de 256 MB no Java.
- **Objetivos (minimizar):** `(p95_fit, -vazão, custo)`, com `custo = cpus + mem_GB` (unidades parecidas com as de nuvem).
  Sem o custo, os dois objetivos de desempenho seriam maximizados simplesmente dando todos os recursos ao contêiner,
  e a busca não teria nada a descobrir. A config. padrão (sem limites) é cobrada como a fatia inteira: 4 núcleos + 2 GB = 6.
- **Carga:** taxa de chegada fixa = 50% da vazão de saturação da config. padrão de cada linguagem (60% ficava perto do
  joelho da curva, onde o p95 é instável). O p95 é a mediana de 2 repetições de 20 s após 15 s de aquecimento.
  A vazão vem de um teste de saturação de 10 s, com 64 VUs no `/cpu` e 1.024 VUs no `/io`
  (com 64 VUs e 20 ms de espera, o teto seria ~3.200 req/s e mediríamos o k6, não o serviço).
- **Penalização:** OOM, crash ou mais de 5% de erros marcam o indivíduo como inválido (`ok=False`, excluído das estatísticas).
  Chegadas descartadas pelo k6 inflam a fitness: `p95_fit = p95 × (1 + 10 × fração descartada)`.
  O CSV guarda separadamente `p95` (bruto), `drop`, `fail` e `p95_fit`. Só a fitness usa o valor penalizado.
- **SLO:** `p95 ≤ 2 × p95 mediano da config. padrão` e nenhuma chegada descartada, por linguagem e endpoint.
  Pergunta operacional: qual o menor custo que atende a mesma carga com latência comparável à padrão?
- **Reavaliação:** o menor p95 visto em 96 medições ruidosas é otimista (o vencedor é em parte sorte). Por isso, `reeval`
  remede, em 5 contêineres novos, duas configs de cada busca: a de menor p95 (`best_p95`) e a mais barata que atendeu
  o SLO (`min_cost_slo`). O H3 usa apenas essas remedições.
- **Baselines:** config. padrão (sem limites de CPU/memória e sem ajuste), config. manual (4 CPUs, 1 GB, 4 workers),
  config. padrão a uma taxa comum a todas as linguagens (50% da saturação da mais lenta) e busca aleatória.

## Análise (`analysis/analyze.py [pasta]`)
- **Qualidade:** coeficiente de variação das repetições (alerta > 10%), taxa / vazão medida da padrão (alerta > 80%,
  indica serviço sobrecarregado) e CPU do k6 (alerta > 300% de 400%, indica que o gerador pode ser o gargalo).
- **H1:** Kruskal-Wallis com tamanho de efeito ε² para a vazão de saturação, o p95 a 50% da própria capacidade e o p95 na
  taxa comum, mais delta de Cliff par a par. Atenção: o p95 a 50% da própria capacidade compara cargas absolutas diferentes
  (por exemplo, Python a ~85 req/s e Go a ~7.000 req/s). Ele mede a latência na mesma utilização relativa. A comparação na mesma carga é a da taxa comum.
- **H2:** hipervolume 3-D normalizado (log p95, vazão, custo) por semente, Mann-Whitney unilateral (GA > aleatória) e delta de Cliff.
  Também reporta o custo da config. mais barata que atendeu o SLO durante a busca.
- **H3:** a partir das remedições, a redução do p95 em relação à padrão com IC 95% por bootstrap, Mann-Whitney e delta de Cliff,
  a economia de custo da `min_cost_slo` com a fração de remedições que de fato atendem o SLO, e Kruskal-Wallis
  entre linguagens dos ganhos por semente do GA.

## Decisões e limitações
- A configuração "padrão" não tem limite de CPU/memória, mas fica fixada nos núcleos 0-3 para o gerador de carga nunca competir com o serviço.
- A config. padrão de Python (1 worker gunicorn × 4 threads) e de Node (1 processo) é de processo único. Isso é o padrão
  real dessas plataformas, mas favorece H3 por construção: boa parte do ganho nelas vem apenas de usar mais de um núcleo.
- A config. manual do Java no `/io` (4 workers bloqueantes) tem teto de 4 / 20 ms = 200 req/s, abaixo da taxa de chegada.
  No Mac, isso deu p95 de ~24 s. É um achado real (o ajuste manual ingênuo pode piorar muito o serviço), não um defeito do experimento.
- Os endpoints não alocam memória (`fib` e `sleep`), então o gene `mem_mb` só pesa pelo OOM e, no Java, pelo heap. Espera-se
  que o GA convirja para a menor memória que não quebra o serviço, o que aparece como economia de custo.
- Genomas idênticos dentro de uma mesma execução são medidos uma vez (cache), mas contam no orçamento.
- O endpoint de I/O usa espera simulada de 20 ms, sem banco de dados.
- O serviço Java usou inicialmente o `HttpServer` embutido do JDK, que limitava a vazão em ~10× (1.495 contra 15.286 req/s no `/cpu`).
  Foi trocado pelo Undertow para a comparação entre linguagens ser justa.
- Antes do experimento real: fixar as imagens base por digest e colocar o governor de CPU em `performance`.
- O Docker Desktop no macOS roda numa VM. Serve para teste de fumaça, mas não para as medições finais.

## Estado atual
- Pipeline validado de ponta a ponta no Mac (imagens, calibração, baselines, GA de 4 × 2 em cada linguagem no `/cpu`).
  O fluxo busca → reeval → análise foi testado com um avaliador simulado.
- As medições no Mac (`results/mac/`) mostraram por que as verificações de qualidade são necessárias:
  - O Java `/cpu` foi medido com taxa a 144% da vazão observada: a calibração mediu 14.430 req/s e as baselines, ~6.000.
  - Go `/cpu` padrão e manual são quase a mesma config. (um teste A/A), mas o p95 variou de 1,2 a 495 ms (CV > 100%).
  - No `/io`, a vazão de Go e Node estava limitada pelos 64 VUs do k6.
- Falta: calibração, baselines (com verificação de qualidade), GA, busca aleatória e reavaliação no host Linux dedicado.
