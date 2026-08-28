#!/usr/bin/env bash

USL_OLLAMA_RELEASE_MODEL="usl-bge-m3:documents-20260824-rc1"
USL_OLLAMA_RELEASE_MANIFEST_SHA256="7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab"

usl_prepare_ollama_runtime() {
  local root="$1"
  local requested="${USL_OLLAMA_RUNTIME:-auto}"
  local host_url="${USL_NATIVE_OLLAMA_HOST_URL:-http://127.0.0.1:11434}"
  local models_root manifest actual

  case "$requested" in
    auto|native|container) ;;
    *)
      printf 'USL_OLLAMA_RUNTIME must be auto, native, or container.\n' >&2
      return 2
      ;;
  esac

  export USL_OLLAMA_RUNTIME_SELECTED=container
  USL_OLLAMA_COMPOSE_OVERRIDE=""
  if [[ "$requested" == container ]]; then
    return
  fi
  if [[ "$(uname -s)" != Darwin || ! -x "$(command -v ollama 2>/dev/null || true)" ]]; then
    if [[ "$requested" == native ]]; then
      printf 'Native Ollama was requested but is not installed on macOS.\n' >&2
      return 2
    fi
    return
  fi
  if ! curl --fail --silent --max-time 5 "$host_url/api/version" >/dev/null; then
    printf '%s\n' \
      'Native Ollama is installed on macOS but is not reachable on loopback.' \
      'Start the Ollama app or `ollama serve`; refusing a silent CPU-container fallback.' >&2
    return 2
  fi

  models_root="${OLLAMA_MODELS:-${HOME}/.ollama/models}"
  manifest="$models_root/manifests/registry.ollama.ai/library/usl-bge-m3/documents-20260824-rc1"
  if [[ ! -f "$manifest" ]]; then
    printf 'Native Ollama is missing %s.\n' "$USL_OLLAMA_RELEASE_MODEL" >&2
    printf 'Install the qualified alias before starting Documents work.\n' >&2
    return 2
  fi
  actual="$(shasum -a 256 "$manifest" | awk '{print $1}')"
  if [[ "$actual" != "$USL_OLLAMA_RELEASE_MANIFEST_SHA256" ]]; then
    printf 'Native Ollama BGE-M3 manifest is not the qualified release.\n' >&2
    return 2
  fi

  export USL_OLLAMA_RUNTIME_SELECTED=native
  export USL_NATIVE_OLLAMA_CONTAINER_URL="${USL_NATIVE_OLLAMA_CONTAINER_URL:-http://host.docker.internal:11434}"
  USL_OLLAMA_COMPOSE_OVERRIDE="$root/compose.ollama-native.yaml"
  printf 'Ollama runtime: native macOS Metal (%s).\n' \
    "$(curl --fail --silent --max-time 5 "$host_url/api/version" \
      | sed -n 's/.*"version":"\([^"]*\)".*/\1/p')"
}
