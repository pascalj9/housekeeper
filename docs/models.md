# Local models

Housekeeper runs every model on-device. This doc explains *which* models, *why*
each was chosen, and *where* they live, so the rest of the codebase can stay
focused on the perception/agent logic.

## Backends

| Backend | Used for | Where it works |
|---|---|---|
| **Ollama** | LLM brain, small LLM, Tier-1 VLM (Moondream), embeddings | macOS, Linux, WSL |
| **MLX** | Tier-2 VLM (Qwen2.5-VL-7B) | Apple Silicon only |

Two-backend split is deliberate:
- **Ollama** is the universal "good enough, easy" runtime. It works the same
  on the Mac and on a WSL dev box, so the perception/agent code path is
  identical in dev and prod. It's the only backend required for the MVP
  (Phases 1–5).
- **MLX** is added in Phase 6 for the heavier Tier-2 VLM, where Ollama's
  vision-language support lags behind native-Metal MLX. Tier-2 is therefore
  intentionally **unavailable on WSL** — dev boxes lose nothing else.

## Model registry

The single source of truth is [`configs/models.yaml`](../configs/models.yaml).
Both the bootstrap script and the Python loader read it. Logical keys
(`vlm_fast`, `embed`, `llm_small`, `llm_brain`, `vlm_smart`) are stable; the
underlying model names can swap without touching the rest of the code.

### Current line-up

| Key         | Backend | Model                                                   | ~GB | Role                              | First needed |
|-------------|---------|---------------------------------------------------------|-----|-----------------------------------|--------------|
| `vlm_fast`  | ollama  | `moondream`                                             | 1.7 | Tier-1 VLM (caption every event)  | Phase 1      |
| `embed`     | ollama  | `nomic-embed-text`                                      | 0.3 | Text embeddings for semantic recall | Phase 3    |
| `llm_small` | ollama  | `qwen2.5:3b`                                            | 1.9 | NL-rule eval, summaries           | Phase 3      |
| `llm_brain` | ollama  | `qwen2.5:7b-instruct`                                   | 4.7 | Agent reasoning + tool calls      | Phase 4      |
| `vlm_smart` | mlx     | `mlx-community/Qwen2.5-VL-7B-Instruct-4bit`             | 6.0 | Tier-2 on-demand VLM              | Phase 6      |

Once the Mac is benchmarked, `llm_brain` may be swapped for
`qwen2.5:14b-instruct` (~9 GB) — change happens in `configs/models.yaml` only.

## Profiles

Profiles let `./scripts/bootstrap_models.sh` and the verifier work with a
subset of models, so a WSL dev box doesn't have to download multi-GB weights
it can't usefully run.

| Profile    | Includes                                              | Total ~GB | Where it's the default |
|------------|-------------------------------------------------------|-----------|------------------------|
| `minimal`  | `vlm_fast`, `embed`, `llm_small`                      | ~4        | WSL / non-Apple-Silicon |
| `standard` | adds `llm_brain`                                      | ~9        | Apple Silicon (default) |
| `full`     | adds `vlm_smart`                                      | ~15       | Mac, after Phase 6     |

`default_profile()` in `housekeeper.models` returns `standard` on Apple Silicon
and `minimal` everywhere else.

## Bootstrapping

```bash
./scripts/bootstrap_models.sh                 # auto profile for this host
./scripts/bootstrap_models.sh -p full         # pull everything (Mac, Phase 6+)
./scripts/bootstrap_models.sh --dry-run       # show what would be pulled
```

The script is idempotent — `ollama pull` is a no-op when the tag is already
present, so re-running is safe.

## Verifying

```bash
uv run housekeeper models list                # show the registry
uv run housekeeper models verify              # check the default profile
uv run housekeeper models verify -p full      # check a specific profile
```

`models verify` exits **1** if any model is missing/unreachable. It will be
the model probe inside `housekeeper doctor` (Phase 0.6).

## Memory budget on the M3 (36 GB)

Rough planning numbers — refine with `housekeeper status` once Phase 8 lands:

| Component                          | RAM (warm)    |
|------------------------------------|---------------|
| `llm_brain` (Qwen2.5-7B 4-bit)     | ~5 GB         |
| `vlm_fast` (Moondream)             | ~2 GB         |
| `vlm_smart` (Qwen2.5-VL-7B, lazy)  | ~7 GB on use, 0 otherwise |
| `embed` + `llm_small`              | ~3 GB combined when warm |
| Housekeeper Python services        | ~1 GB total   |
| macOS + headroom                   | leave ≥ 8 GB  |

If we bump `llm_brain` to 14B (~9 GB) we lose ~4 GB of headroom — fine as long
as `vlm_smart` stays lazy-loaded and gets evicted when idle.

## Privacy

All model traffic stays on `127.0.0.1`. The bootstrap script is the **only**
component that talks to the public internet, and it only does so when pulling
weights. After bootstrap, the system runs without any outbound HTTP.
