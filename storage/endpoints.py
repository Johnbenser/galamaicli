ENDPOINT_REGISTRY = {
    "Cloudflare": {
        "base_url": "https://api.cloudflare.com/client/v4/accounts/{account_id}",
        "api_key_env": "CLOUDFLARE_API_TOKEN",
        "models": {
            "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b": {
                "route_type": "v1",
                "name": "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b",
                "display_name": "DeepSeek R1 Distill 32B",
                "context_window": 64000,
                "supports_streaming": True
            },
            "@cf/qwen/qwen2.5-coder-32b-instruct": {
                "route_type": "v1",
                "name": "@cf/qwen/qwen2.5-coder-32b-instruct",
                "display_name": "Qwen 2.5 Coder 32B",
                "context_window": 32768,
                "supports_streaming": True
            },
            "@cf/qwen/qwen3.8-27b": {
                "route_type": "v1",
                "name": "@cf/qwen/qwen3.8-27b",
                "display_name": "Qwen 3.8 27B CF",
                "context_window": 32768,
                "supports_streaming": True
            },
            "@cf/zai-org/glm-4.7-flash": {
                "route_type": "v1",
                "name": "@cf/zai-org/glm-4.7-flash",
                "display_name": "GLM 4.7 Flash",
                "context_window": 128000,
                "supports_streaming": True
            },
            "@cf/meta/llama-4-scout-17b-16e-instruct": {
                "route_type": "v1",
                "name": "@cf/meta/llama-4-scout-17b-16e-instruct",
                "display_name": "Llama 4 Scout 17B",
                "context_window": 128000,
                "supports_streaming": True
            },
            "@cf/mistral/mistral-7b-instruct-v0.2-lora": {
                "route_type": "v1",
                "name": "@cf/mistral/mistral-7b-instruct-v0.2-lora",
                "display_name": "Mistral 7B LoRA",
                "context_window": 32768,
                "supports_streaming": True
            }
        }
    },
    "NVIDIA": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_key_env": "NVIDIA_API_KEY",
        "models": {
            "deepseek-ai/deepseek-v4.1-flash": {
                "name": "deepseek-ai/deepseek-v4.1-flash",
                "display_name": "DeepSeek V4.1 Flash",
                "context_window": 262144,
                "supports_streaming": True
            },
            "z-ai/glm-5.3": {
                "name": "z-ai/glm-5.3",
                "display_name": "GLM 5.3",
                "context_window": 128000,
                "supports_streaming": True
            },
            "z-ai/glm-5.3-flash": {
                "name": "z-ai/glm-5.3-flash",
                "display_name": "GLM 5.3 Flash",
                "context_window": 128000,
                "supports_streaming": True
            },
            "moonshotai/kimi-k3": {
                "name": "moonshotai/kimi-k3",
                "display_name": "Kimi K3 (NVIDIA)",
                "context_window": 128000,
                "supports_streaming": True
            },
            "nvidia/nemotron-3.5-lightning-30b-a3b": {
                "name": "nvidia/nemotron-3.5-lightning-30b-a3b",
                "display_name": "Nemotron 3.5 Lightning",
                "context_window": 128000,
                "supports_streaming": True
            },
            "nvidia/nemotron-3-ultra-550b-a55b": {
                "name": "nvidia/nemotron-3-ultra-550b-a55b",
                "display_name": "Nemotron 3 Ultra 550B",
                "context_window": 128000,
                "supports_streaming": True
            },
            "poolside/laguna-xs-2.1": {
                "name": "poolside/laguna-xs-2.1",
                "display_name": "Laguna XS 2.1",
                "context_window": 32768,
                "supports_streaming": True
            },
            "google/gemma-4-31b-it": {
                "name": "google/gemma-4-31b-it",
                "display_name": "Gemma 4 31B IT",
                "context_window": 32768,
                "supports_streaming": True
            }
        }
    },
    "OpenRouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "models": {
            "nvidia/nemotron-3.5-lightning:free": {
                "name": "nvidia/nemotron-3.5-lightning:free",
                "display_name": "Nemotron 3.5 Lightning (Free)",
                "context_window": 128000,
                "supports_streaming": True
            },
            "nvidia/nemotron-3-super-120b-a12b:free": {
                "name": "nvidia/nemotron-3-super-120b-a12b:free",
                "display_name": "Nemotron 3 Super 120B (Free)",
                "context_window": 128000,
                "supports_streaming": True
            },
            "nvidia/nemotron-3-ultra-550b-a55b:free": {
                "name": "nvidia/nemotron-3-ultra-550b-a55b:free",
                "display_name": "Nemotron 3 Ultra 550B (Free)",
                "context_window": 128000,
                "supports_streaming": True
            },
            "cohere/north-mini-code:free": {
                "name": "cohere/north-mini-code:free",
                "display_name": "North Mini Code (Free)",
                "context_window": 64000,
                "supports_streaming": True
            },
            "liquid/lfm-2.5-2.6b:free": {
                "name": "liquid/lfm-2.5-2.6b:free",
                "display_name": "Liquid LFM 2.5 (Fast Free)",
                "context_window": 32768,
                "supports_streaming": True
            },
            "dots-studio/dots-3-note-preview:free": {
                "name": "dots-studio/dots-3-note-preview:free",
                "display_name": "Dots 3 Note Preview (Free)",
                "context_window": 32768,
                "supports_streaming": True
            },
            "poolside/laguna-xs-2.1:free": {
                "name": "poolside/laguna-xs-2.1:free",
                "display_name": "Laguna XS 2.1 (Free)",
                "context_window": 32768,
                "supports_streaming": True
            }
        }
    },
    "Groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "models": {
            "openai/gpt-oss-120b": {
                "name": "openai/gpt-oss-120b",
                "display_name": "GPT OSS 120B",
                "context_window": 128000,
                "supports_streaming": True
            },
            "openai/gpt-oss-20b": {
                "name": "openai/gpt-oss-20b",
                "display_name": "GPT OSS 20B",
                "context_window": 128000,
                "supports_streaming": True
            },
            "qwen/qwen3.8-27b": {
                "name": "qwen/qwen3.8-27b",
                "display_name": "Qwen 3.8 27B",
                "context_window": 32768,
                "supports_streaming": True
            }
        }
    },
    "ModelScope": {
        "base_url": "https://api-inference.modelscope.cn/v1",
        "api_key_env": "SCOPE_API_KEY",
        "models": {
            "Qwen/Qwen3.8-27B": {
                "name": "Qwen/Qwen3.8-27B",
                "display_name": "Qwen 3.8 27B Scope",
                "context_window": 32768,
                "supports_streaming": True
            },
            "deepseek-ai/DeepSeek-V4.1-Flash": {
                "name": "deepseek-ai/DeepSeek-V4.1-Flash",
                "display_name": "DeepSeek V4.1 Flash Scope",
                "context_window": 128000,
                "supports_streaming": True
            }
        }
    },
    "Direct": {
        "base_url": "direct",
        "api_key": "direct",
        "models": {
            "gemini-3.5-flash": {
                "provider": "google",
                "name": "gemini-3.5-flash",
                "display_name": "Gemini 3.5 Flash",
                "context_window": 1048576,
                "supports_streaming": True
            },
            "gemini-3.5-flash-lite": {
                "provider": "google",
                "name": "gemini-3.5-flash-lite",
                "display_name": "Gemini 3.5 Flash Lite",
                "context_window": 1048576,
                "supports_streaming": True
            },
            "gemini-2.5-flash": {
                "provider": "google",
                "name": "gemini-2.5-flash",
                "display_name": "Gemini 2.5 Flash",
                "context_window": 1048576,
                "supports_streaming": True
            },
            "gemini-3.8-flash": {
                "provider": "google",
                "name": "gemini-3.8-flash",
                "display_name": "Gemini 3.8 Flash",
                "context_window": 1048576,
                "supports_streaming": True
            },
            "command-a-plus-07-2025": {
                "provider": "cohere",
                "name": "command-a-plus-07-2025",
                "display_name": "Command A+ 07-2025",
                "context_window": 128000,
                "supports_streaming": True
            },
            "north-mini-code-1-0": {
                "provider": "cohere",
                "name": "north-mini-code-1-0",
                "display_name": "North Mini Code 1.0",
                "context_window": 64000,
                "supports_streaming": True
            },
            "command-a-plus-05-2026": {
                "provider": "cohere",
                "name": "command-a-plus-05-2026",
                "display_name": "Command A+ 05-2026",
                "context_window": 128000,
                "supports_streaming": True
            },
            "codestral-latest": {
                "provider": "mistral",
                "name": "codestral-latest",
                "display_name": "Codestral Latest",
                "context_window": 64000,
                "supports_streaming": True
            },
            "mistral-14b-latest": {
                "provider": "mistral",
                "name": "mistral-14b-latest",
                "display_name": "Mistral 14B Latest",
                "context_window": 64000,
                "supports_streaming": True
            },
            "mistral-8b-latest": {
                "provider": "mistral",
                "name": "mistral-8b-latest",
                "display_name": "Mistral 8B Latest",
                "context_window": 64000,
                "supports_streaming": True
            },
            "mistral-3b-latest": {
                "provider": "mistral",
                "name": "mistral-3b-latest",
                "display_name": "Mistral 3B Latest",
                "context_window": 32768,
                "supports_streaming": True
            },
            "mistral-large-latest": {
                "provider": "mistral",
                "name": "mistral-large-latest",
                "display_name": "Mistral Large Latest",
                "context_window": 128000,
                "supports_streaming": True
            },
            "anthropic/claude-sonnet-4.5": {
                "provider": "kilo",
                "name": "anthropic/claude-sonnet-4.5",
                "display_name": "Claude Sonnet 4.5 (Kilo)",
                "context_window": 200000,
                "supports_streaming": True
            },
            "kilo-auto/free": {
                "provider": "kilo",
                "name": "kilo-auto/free",
                "display_name": "Kilo Auto (Free)",
                "context_window": 128000,
                "supports_streaming": True
            },
            "minimax-m2.7": {
                "provider": "llm7",
                "name": "minimax-m2.7",
                "display_name": "MiniMax M2.7",
                "context_window": 128000,
                "supports_streaming": True
            },
            "mistral-Nemo-Instruct-2407": {
                "provider": "llm7",
                "name": "mistral-Nemo-Instruct-2407",
                "display_name": "Mistral Nemo Instruct",
                "context_window": 128000,
                "supports_streaming": True
            }
        }
    },
    "Agent Router": {
        "base_url": "https://agentrouter.org/v1",
        "api_key_env": "AGENT_ROUTER_API_KEY",
        "routes": {
            "chat_completions": "https://agentrouter.org/v1/chat/completions",
            "models": "https://agentrouter.org/v1beta/models",
            "responses": "https://agentrouter.org/v1/responses",
            "embeddings": "https://agentrouter.org/v1/embeddings",
            "messages": "https://agentrouter.org/v1/messages"
        },
        "models": {
            "deepseek-v4-flash": {"name": "deepseek-v4-flash", "display_name": "DeepSeek V4 Flash", "supports_streaming": True},
            "gpt-6-astra": {"name": "gpt-6-astra", "display_name": "GPT 6 Astra", "supports_streaming": True},
            "gpt-6.1-sol": {"name": "gpt-6.1-sol", "display_name": "GPT 6.1 Sol", "supports_streaming": True},
            "claude-opus-4-8": {"name": "claude-opus-4-8", "display_name": "Claude Opus 4.8", "supports_streaming": True},
            "claude-opus-5": {"name": "claude-opus-5", "display_name": "Claude Opus 5", "supports_streaming": True}
        }
    },
    "Local Endpoint": {
        "base_url": "http://127.0.0.1:8080/v1",
        "api_key_env": "LOCAL_API_KEY",
        "models": {
            "/data/data/com.termux/files/home/Qwen2.5-Coder-7B-Instruct-abliterated.i1-Q4_K_M.gguf": {
                "name": "/data/data/com.termux/files/home/Qwen2.5-Coder-7B-Instruct-abliterated.i1-Q4_K_M.gguf",
                "display_name": "Qwen 2.5 Coder 7B Abliterated (Local)",
                "context_window": 8192,
                "supports_streaming": True
            }
        }
    }
}


# Private, home-owned custom connections overlay the public provider catalog.
from storage.user_endpoints import load_into as _load_user_endpoints
_load_user_endpoints(ENDPOINT_REGISTRY)
