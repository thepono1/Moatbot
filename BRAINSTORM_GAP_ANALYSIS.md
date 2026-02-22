# Moatbot Gap Analysis & Brainstorming Plan

> **Date:** 2026-02-22
> **Purpose:** Reference doc for comparing against the alpha engine branch to identify real gaps
> **Status:** BRAINSTORM — nothing here is committed code, just research + recommendations

---

## Table of Contents

1. [Current State Assessment](#1-current-state-assessment)
2. [MCP Token Mitigation](#2-mcp-token-mitigation)
3. [Multi-Model Swarm Architecture (Kimi + Opus + Haiku)](#3-multi-model-swarm-architecture)
4. [Shared Memory System](#4-shared-memory-system)
5. [Voice Pipeline (Free ElevenLabs Replacement)](#5-voice-pipeline)
6. [Computer Use Agent](#6-computer-use-agent)
7. [No-Code Builder](#7-no-code-builder)
8. [Gap Priority Matrix](#8-gap-priority-matrix)
9. [Open-Source TTS Full Research](#9-open-source-tts-full-research)
10. [Sources](#10-sources)

---

## 1. Current State Assessment

### What exists in this repo (Moatbot)

- **Backend:** Discord bot shell (`backend/`), some agent scaffolding (Kaleo, Solomon, Deborah references)
- **Frontend:** React app shell (`frontend/`)
- **Memory:** Placeholder directory (`memory/`)
- **Tests:** Basic test scaffolding (`tests/`, `test_reports/`)
- **Infra:** Dockerfile, Railway config

### What's missing (potential gaps to verify against alpha engine)

- [ ] Agent runtime — do agents actually execute tasks end-to-end?
- [ ] Shared memory / state between agents
- [ ] MCP server connections + token optimization
- [ ] Multi-provider model routing (Kimi, Opus, Haiku, Sonnet)
- [ ] Voice pipeline (TTS, cloning, WhatsApp integration)
- [ ] Computer Use agent
- [ ] RAG / knowledge base
- [ ] No-code workflow builder UI
- [ ] Frontend dashboard (beyond shell)

**Action:** Compare this list against the alpha engine branch to see which gaps are already filled.

---

## 2. MCP Token Mitigation

### The Problem

MCPs bleed tokens in three ways:
1. **Tool schemas** — 500-2000 tokens per tool definition, loaded every turn
2. **Intermediate results** — raw JSON responses bloat context
3. **Multi-turn accumulation** — schemas + results compound across conversation

With 20 servers x 20 tools = 400 tools, you're burning **50-150K tokens before the user even speaks**.

### Strategies Ranked by Impact

| Strategy | Token Savings | Effort | Description |
|---|---|---|---|
| **Code Execution Mode** | ~98.7% | High | Present tools as code stubs, not JSON schemas |
| **Dynamic Toolsets** | ~96% | Medium | search → describe → execute (3-step pattern) |
| **Tool Search** (Claude Code built-in) | ~85% on definitions | Free | Triggers automatically at >10% context usage |
| **Response Filtering** | ~93-98% per response | Medium | Field whitelisting, server-side aggregation |
| **Semantic Caching** | Eliminates redundant calls | Medium | Reuse answers for similar queries |
| **MCP Gateway/Proxy** | Varies | High | Centralized cache + filter + batch layer |

### Recommended Build: MCP Gateway

An MCP gateway service sitting between agents and all MCP servers:

```
Agents → MCP Gateway → MCP Servers
              │
              ├── Dynamic tool loading (only load what's needed)
              ├── Response filtering (strip unnecessary fields)
              ├── Multi-tier cache (Redis hot / disk warm)
              ├── Request batching (combine related calls)
              └── Token budget tracking per conversation
```

**Expected result:** 90%+ token cost reduction.

### Key Techniques

1. **Lazy tool loading:** Don't send all 400 tool schemas. Send a `search_tools` meta-tool. Agent searches → gets 3-5 relevant tools → those schemas load.
2. **Response projection:** Server-side `fields` parameter. Request only the fields you need. `GET /api/data?fields=name,status` instead of getting the full object.
3. **Semantic cache:** Hash the intent of a query (not exact match). "What's the weather in NYC?" and "NYC weather?" hit the same cache entry. TTL-based invalidation.
4. **Context compression:** Summarize MCP results before injecting into agent context. A 10K-token API response becomes a 200-token summary.

---

## 3. Multi-Model Swarm Architecture

### The Concept

Route tasks to the cheapest model that can handle them. Use Kimi K2.5 swarms for parallel research, Opus for deep reasoning, Haiku for routing/filtering.

### Architecture

```
                ┌──────────────────────┐
                │  Meta-Orchestrator   │
                │  (Moatbot Deborah)   │
                └──────────┬───────────┘
                           │
             ┌─────────────┼─────────────┐
             │             │             │
        ┌────▼─────┐  ┌────▼─────┐  ┌───▼──────┐
        │ Kimi K2.5│  │ Opus/    │  │ Haiku    │
        │ Swarm    │  │ Sonnet   │  │ (Router) │
        │          │  │ Team     │  │          │
        │ Web      │  │ Code,    │  │ Classify,│
        │ research,│  │ Computer │  │ filter,  │
        │ data     │  │ Use,     │  │ simple   │
        │ gather   │  │ deep     │  │ tasks    │
        └────┬─────┘  └────┬─────┘  └───┬──────┘
             │             │             │
             └─────────────┼─────────────┘
                           │
                ┌──────────▼───────────┐
                │  Shared State Store  │
                │  (Redis + MongoDB +  │
                │   Vector DB)         │
                └──────────────────────┘
```

### Cost Comparison (per 1M tokens)

| Model | Input | Output | Best For |
|---|---|---|---|
| **Kimi K2.5** | $0.60 ($0.10 cached) | $3.00 | Parallel research, data gathering, bulk tasks |
| **Opus 4.6** | $15.00 | $75.00 | Deep reasoning, complex code, architecture |
| **Sonnet 4.6** | $3.00 | $15.00 | Balanced tasks, code generation |
| **Haiku 4.5** | $0.80 | $4.00 | Routing, classification, simple transforms |

### Kimi K2.5 Details

- **Released:** Jan 2026, 1T MoE model (32B activated)
- **Native Agent Swarm:** Up to 100 sub-agents, 1,500 coordinated tool calls
- **76% cheaper than Opus** for equivalent tasks
- **Open weights** available
- **Best for:** Parallelizable research, data collection, web scraping, document processing

### Routing Logic (Haiku as Router)

```python
# Pseudocode for intelligent routing
def route_task(task):
    complexity = haiku.classify(task)  # cheap classification

    if complexity == "simple":
        return haiku.execute(task)          # $0.80/M tokens
    elif complexity == "research":
        return kimi_swarm.execute(task)     # $0.60/M tokens, parallel
    elif complexity == "code" or complexity == "balanced":
        return sonnet.execute(task)         # $3.00/M tokens
    elif complexity == "deep_reasoning":
        return opus.execute(task)           # $15.00/M tokens
```

### Expected Cost Savings

A workflow costing **$50 on Opus-only** could cost **$8-15** with intelligent routing — 70-85% savings.

---

## 4. Shared Memory System

### The Problem

Agents (Kaleo, Solomon, Deborah) currently can't share state efficiently. Everything passes through context windows, which wastes tokens and loses information across sessions.

### Proposed Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   Shared Memory Layer                    │
│                                                         │
│  ┌──────────────┐  ┌────────────────────────────────┐  │
│  │ Private       │  │ Shared Fragments               │  │
│  │ Fragments     │  │ (with provenance tracking &    │  │
│  │ (per-agent    │  │  role-based access control)    │  │
│  │  scratchpad)  │  │                                │  │
│  └──────────────┘  └────────────────────────────────┘  │
│                                                         │
│  Storage Tiers:                                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐     │
│  │ Redis    │  │ MongoDB  │  │ Vector DB        │     │
│  │ (hot     │  │ (persist │  │ (semantic search, │     │
│  │  state,  │  │  memory, │  │  RAG, long-term  │     │
│  │  working │  │  plans,  │  │  knowledge)      │     │
│  │  memory) │  │  logs)   │  │                  │     │
│  └──────────┘  └──────────┘  └──────────────────┘     │
│                                                         │
│  Protocol: A2A (Agent-to-Agent) for cross-provider      │
│  Access: MCP server exposing memory as tools            │
└─────────────────────────────────────────────────────────┘
```

### Memory Types

| Type | Storage | TTL | Example |
|---|---|---|---|
| **Working memory** | Redis | Session-scoped | Current task state, intermediate results |
| **Episodic memory** | MongoDB | Days-weeks | Conversation summaries, decisions made |
| **Semantic memory** | Vector DB | Permanent | Knowledge base, docs, learned patterns |
| **Procedural memory** | MongoDB | Permanent | Workflows, SOPs, how-to templates |

### Interface (MCP Server)

```python
# Memory MCP tools exposed to all agents
tools = [
    "memory.store(key, value, scope, ttl)",
    "memory.retrieve(key_or_query, scope)",
    "memory.search(semantic_query, top_k)",
    "memory.share(fragment_id, target_agents)",
    "memory.summarize(conversation_id)",
]
```

---

## 5. Voice Pipeline

### Goal: $0/month voice cloning + WhatsApp voice messages

### Recommended Stack

| Layer | Tool | License | Cost |
|---|---|---|---|
| **TTS Engine** | Chatterbox Turbo (350M params) | MIT | $0 — self-hosted |
| **Voice Cloning** | Built into Chatterbox (5s of audio) | MIT | $0 |
| **WhatsApp Gateway** | Baileys (WhatsApp Web protocol) | MIT | $0 |
| **Audio Conversion** | FFmpeg (WAV → OGG/Opus) | LGPL | $0 |

### Why Chatterbox Turbo

- Beat ElevenLabs in blind tests (63.8% listener preference)
- MIT license — fully commercial
- ~470ms first-chunk latency on RTX 4090
- Voice cloning from just 5 seconds of audio
- Emotion control + paralinguistic tags (`[laugh]`, `[sigh]`, `[cough]`)
- Docker + OpenAI-compatible API available
- 350M parameters — lightweight, runs on 4-6 GB VRAM

### Alternative TTS Engines (if needed)

| Model | Params | Voice Clone | Latency | License | Best For |
|---|---|---|---|---|---|
| **Chatterbox Turbo** | 350M | Yes (5s) | ~470ms | MIT | Best all-around |
| **Qwen3-TTS 0.6B** | 600M | Yes (3s) | ~97ms | Apache 2.0 | Lowest latency |
| **Fish Speech S1** | 4B | Yes (10-30s) | Higher | Apache 2.0* | Best raw quality (#1 TTS-Arena) |
| **CosyVoice 3** | 500M | Yes | Ultra-low | Apache 2.0 | Real-time streaming |
| **GPT-SoVITS v3** | Variable | Yes (1min) | Fast | MIT | Best with fine-tuning |
| **Orpheus 3B** | 3B | No | Moderate | Apache 2.0 | Best emotions (no cloning) |
| **Piper** | Tiny | No* | <100ms | GPL | Raspberry Pi / no GPU |

*Fish Speech full S1 weights are CC-BY-NC; mini is Apache 2.0
*Piper can clone via TextyMcSpeechy fine-tuning tool

### Pipeline Architecture

```
User sends WhatsApp text message
        │
        ▼
┌───────────────┐     ┌──────────────────┐     ┌──────────┐
│ Baileys       │────▶│ TTS API          │────▶│ FFmpeg   │
│ (WhatsApp     │     │ (Chatterbox      │     │ WAV →    │
│  WebSocket)   │     │  Turbo server)   │     │ OGG/Opus │
└───────────────┘     └──────────────────┘     └────┬─────┘
        ▲                                           │
        │                                           ▼
        └───────────────────────────────────────────┘
              Send back as voice note (ptt: true)
```

### Key WhatsApp Voice Note Requirements

- Audio **must** be OGG format with Opus codec
- MIME type: `audio/ogg; codecs=opus`
- `ptt: true` flag → displays as voice note (waveform) not audio file

### Baileys Code Skeleton

```javascript
const { makeWASocket, useMultiFileAuthState } = require('@whiskeysockets/baileys');

sock.ev.on('messages.upsert', async ({ messages }) => {
    const text = messages[0].message?.conversation;
    if (text) {
        // 1. Call self-hosted Chatterbox API
        const audio = await fetch('http://localhost:8000/tts', {
            method: 'POST',
            body: JSON.stringify({ text, speaker_wav: './my_voice.wav' })
        });
        // 2. Convert to OGG/Opus via FFmpeg
        // 3. Send as voice note
        await sock.sendMessage(jid, {
            audio: oggBuffer,
            mimetype: 'audio/ogg; codecs=opus',
            ptt: true
        });
    }
});
```

### Risks

- **Baileys is unofficial** — WhatsApp could break protocol or ban your number
- **No SLA** — you maintain everything
- **GPU needed for quality** — CPU inference works but is slow (5-30s per sentence vs <1s on GPU)
- **WhatsApp ToS** — automated messaging via unofficial APIs may violate terms

---

## 6. Computer Use Agent

### What's Available

Claude Sonnet 4.5+ has Computer Use in beta. Can maintain focus for 30+ hours on GUI tasks.

### Proposed Architecture

```
┌──────────────────────────────────────┐
│  Computer Use Agent Container        │
│                                      │
│  ┌──────────┐  ┌─────────────────┐  │
│  │ Xvfb     │  │ Claude Computer │  │
│  │ (virtual │  │ Use API         │  │
│  │  display) │  │                 │  │
│  └──────────┘  └─────────────────┘  │
│                                      │
│  ┌──────────┐  ┌─────────────────┐  │
│  │ Chromium │  │ Screenshot      │  │
│  │ Browser  │  │ capture loop    │  │
│  └──────────┘  └─────────────────┘  │
│                                      │
│  Sandboxed Docker container          │
│  No access to host filesystem        │
└──────────────────────────────────────┘
```

### Use Cases for Moatbot

- Automating GUI-based workflows users describe
- Testing web apps visually
- Interacting with legacy apps that have no API
- Filling forms, downloading reports, taking screenshots

### Integration

- 4th agent alongside Kaleo/Solomon/Deborah
- Deborah (orchestrator) dispatches GUI tasks to Computer Use agent
- Results flow back through shared memory layer

---

## 7. No-Code Builder

### Current Landscape

Nothing exists that combines multi-model swarms + MCP optimization + Computer Use + voice in a no-code interface.

### Closest Platforms

| Platform | Type | Price | Limitation |
|---|---|---|---|
| **n8n** | Self-hosted, open source | Free | No agent swarm support |
| **Lindy.ai** | Multi-agent collaboration | $49/mo | Closed source |
| **Relay.app** | Human-in-the-loop workflows | Paid | Limited integrations |
| **Flowise** | LangChain visual builder | Free | Single-model focus |

### What to Build

A visual node-and-flow editor on top of Moatbot's orchestration engine:

```
┌─────────────────────────────────────────┐
│  No-Code Builder (React Frontend)       │
│                                         │
│  ┌─────┐   ┌─────┐   ┌─────┐          │
│  │Input│──▶│Agent│──▶│Voice│          │
│  │Node │   │Node │   │Node │          │
│  └─────┘   └─────┘   └─────┘          │
│       drag-and-drop workflow canvas     │
│                                         │
│  Node types:                            │
│  - Trigger (WhatsApp msg, Discord,      │
│    webhook, schedule)                   │
│  - Agent (Kimi, Opus, Sonnet, Haiku)    │
│  - Tool (MCP server call)               │
│  - Voice (TTS, STT)                     │
│  - Computer Use (GUI task)              │
│  - Logic (if/else, loop, parallel)      │
│  - Memory (store, retrieve, search)     │
└─────────────────────────────────────────┘
```

**Priority: LOW** — build this last, after all the underlying systems work.

---

## 8. Gap Priority Matrix

| # | Gap | Priority | Effort | Depends On | Notes |
|---|---|---|---|---|---|
| 1 | **Agent runtime** (do agents execute?) | CRITICAL | Medium | Nothing | Unblocks everything |
| 2 | **Shared memory system** | HIGH | Medium | #1 | Redis + MongoDB + Vector DB |
| 3 | **MCP gateway + token optimization** | HIGH | Medium | #1 | 90%+ token savings |
| 4 | **Voice pipeline** (Chatterbox + Baileys) | MEDIUM | Medium | #1 | $0/month ElevenLabs replacement |
| 5 | **Multi-provider swarm** (Kimi + Opus + Haiku) | HIGH | High | #1, #2 | 70-85% cost savings |
| 6 | **Computer Use agent** | MEDIUM | Medium | #1, #2 | Sandboxed Docker |
| 7 | **RAG / Knowledge base** | MEDIUM | Medium | #2 | Vector DB from shared memory |
| 8 | **No-code workflow builder** | LOW | High | Everything | Build last |
| 9 | **Frontend dashboard** | LOW | Medium | #1 | Currently a shell |

### Recommended Build Order

```
Phase 1 (Foundation):  #1 Agent runtime
Phase 2 (Core):        #2 Shared memory  +  #3 MCP gateway  (parallel)
Phase 3 (Features):    #4 Voice  +  #5 Swarms  +  #6 Computer Use  (parallel)
Phase 4 (Knowledge):   #7 RAG
Phase 5 (Polish):      #8 No-code builder  +  #9 Dashboard
```

---

## 9. Open-Source TTS Full Research

### Tier 1: Best Overall (Recommended)

#### Chatterbox / Chatterbox Turbo (Resemble AI)
- **Params:** 350M (Turbo) / 500M (Original/Multilingual)
- **Voice clone:** Yes, zero-shot from ~5 seconds
- **Languages:** 23 (Multilingual), English (Original)
- **License:** MIT
- **VRAM:** ~4-6 GB (Turbo), ~6-8 GB (Original)
- **Latency:** ~0.47s to first chunk on RTX 4090
- **GitHub:** resemble-ai/chatterbox
- **Status:** Actively maintained (Feb 2026)
- **Key:** Beat ElevenLabs in blind tests (63.8% preference). Emotion exaggeration control. Paralinguistic tags.

#### Fish Speech / OpenAudio S1 (Fish Audio)
- **Params:** 4B (S1 full), 0.5B (S1-mini)
- **Voice clone:** Yes, from 10-30 seconds
- **Languages:** 8+ (EN, ZH, JA, KO, FR, DE, AR, ES)
- **License:** Apache 2.0 (repo), CC-BY-NC (full S1 weights)
- **VRAM:** ~4 GB minimum
- **GitHub:** fishaudio/fish-speech (24,967 stars)
- **Status:** Very actively maintained (Feb 2026)
- **Key:** #1 on TTS-Arena. 0.008 WER on English. Emotion tags.

#### Qwen3-TTS (Alibaba)
- **Params:** 0.6B / 1.7B
- **Voice clone:** Yes, from ~3 seconds
- **Languages:** 10
- **License:** Apache 2.0
- **VRAM:** ~8 GB (0.6B), ~16 GB (1.7B)
- **Latency:** 97ms first-packet (best streaming latency of any model)
- **Status:** Released Jan 22, 2026. Active development.
- **Key:** VoiceDesign — describe a voice in natural language. 5M+ hours training data.

### Tier 2: Strong Contenders

#### CosyVoice 2/3 (Alibaba SpeechLab)
- **Params:** 0.5B
- **Voice clone:** Yes, zero-shot
- **Languages:** 9+ languages, 18+ dialects (v3)
- **License:** Apache 2.0
- **Status:** Actively maintained. vLLM acceleration (4x speedup).

#### GPT-SoVITS (RVC-Boss)
- **Params:** Variable (GPT + VITS hybrid)
- **Voice clone:** Yes, 1 minute for high quality, 5 seconds basic
- **Languages:** ZH, EN, JA, KO, Cantonese
- **License:** MIT
- **VRAM:** ~8 GB
- **Status:** Actively maintained, V3 rivals commercial systems.

#### Orpheus TTS (Canopy AI)
- **Params:** 150M / 400M / 1B / 3B
- **Voice clone:** No (preset voices only)
- **License:** Apache 2.0
- **Key:** Built on Llama-3.2. Best emotional speech. Runs via LM Studio.

### Tier 3: Niche / Aging

| Model | Clone | License | Note |
|---|---|---|---|
| **XTTS v2** (Coqui) | Yes (6s, 17 langs) | Non-commercial | Company dead since 2024. Community-maintained. |
| **Piper** | No (needs fine-tune) | GPL | Runs on Raspberry Pi. Sub-second. |
| **OpenVoice** (MIT/MyShell) | Yes (30s) | MIT | Lightweight but accents get flattened. |
| **Bark** (Suno) | Limited | MIT | Stagnant. Inconsistent quality. |
| **StyleTTS2** | Yes | MIT | Superseded by Kokoro. |
| **Kokoro** | No | Apache 2.0 | 82M params, 96x real-time. No cloning. |

### Hardware Requirements

| Model | Min GPU VRAM | CPU-Only? | Raspberry Pi? |
|---|---|---|---|
| Chatterbox Turbo | ~4-6 GB | Yes (slow) | No |
| Fish Speech S1-mini | ~4 GB | Possible | No |
| Qwen3-TTS 0.6B | ~8 GB | Not recommended | No |
| CosyVoice2 | ~4-6 GB | Possible | No |
| GPT-SoVITS | ~8 GB | Yes (slow) | No |
| Orpheus 3B | ~6-8 GB | Via LM Studio | No |
| Piper | <1 GB | Yes (fast) | Yes |
| Kokoro | <1 GB | Yes (fast) | Yes |

### Quality vs ElevenLabs

| Model | vs. ElevenLabs | Notes |
|---|---|---|
| Fish Speech / OpenAudio S1 | **Equal or better** | #1 on TTS-Arena |
| Chatterbox | **Equal or better** | 63.8% listener preference |
| Qwen3-TTS 1.7B | Near-equal | Best streaming latency |
| GPT-SoVITS (fine-tuned) | Near-equal | With 1-min fine-tuning |
| CosyVoice 3 | Near-equal | Excellent multilingual |
| Orpheus 3B | Close | Best emotional, English only |
| XTTS v2 | ~85-90% | Good but aging |
| OpenVoice | ~70-80% | Accent issues |
| Piper | ~70-80% | Optimized for speed, not quality |

---

## 10. Sources

- [Chatterbox - Resemble AI](https://www.resemble.ai/chatterbox/)
- [Chatterbox GitHub](https://github.com/resemble-ai/chatterbox)
- [Chatterbox TTS API (OpenAI-compatible)](https://github.com/travisvn/chatterbox-tts-api)
- [Fish Speech GitHub](https://github.com/fishaudio/fish-speech)
- [OpenAudio S1 Docs](https://speech.fish.audio/)
- [Qwen3-TTS Complete Guide](https://medium.com/@zh.milo/qwen3-tts-the-complete-2026-guide)
- [CosyVoice GitHub](https://github.com/FunAudioLLM/CosyVoice)
- [GPT-SoVITS GitHub](https://github.com/RVC-Boss/GPT-SoVITS)
- [Orpheus TTS GitHub](https://github.com/canopyai/Orpheus-TTS)
- [Piper TTS GitHub](https://github.com/rhasspy/piper)
- [OpenVoice GitHub](https://github.com/myshell-ai/OpenVoice)
- [Baileys GitHub](https://github.com/WhiskeySockets/Baileys)
- [NerdyNav: Best Free ElevenLabs Alternatives](https://nerdynav.com/open-source-ai-voice/)
- [BentoML: Best Open-Source TTS Models 2026](https://www.bentoml.com/blog/exploring-the-world-of-open-source-text-to-speech-models)
- [Modal: Top Open-Source TTS](https://modal.com/blog/open-source-tts)
- [WhatsApp Business Platform Pricing](https://business.whatsapp.com/products/platform-pricing)
- [Inworld: Best TTS APIs for Real-Time Voice Agents 2026](https://inworld.ai/resources/best-voice-ai-tts-apis-for-real-time-voice-agents-2026-benchmarks)
