from dataclasses import dataclass
from typing import Dict, Union

import numpy as np
import torch
from torch import Tensor
from torch import nn

from huggingface_hub import hf_hub_download
from openvino import Core

from .transcribe import transcribe as transcribe_function
from .decoding import detect_language as detect_language_function, decode as decode_function


@dataclass
class ModelDimensions:
    n_mels: int
    n_audio_ctx: int
    n_audio_state: int
    n_audio_head: int
    n_audio_layer: int
    n_vocab: int
    n_text_ctx: int
    n_text_state: int
    n_text_head: int
    n_text_layer: int


class OpenVinoAudioEncoder(nn.Module):
    def __init__(self, model: str):
        super().__init__()

        self.core = Core()
        self._model = self.core.read_model(
            hf_hub_download(repo_id=f"zhuzilin/whisper-openvino-{model}", filename="encoder.xml"),
            hf_hub_download(repo_id=f"zhuzilin/whisper-openvino-{model}", filename="encoder.bin"),
        )
        self.model = self.core.compile_model(self._model, "CPU")

    def forward(self, x: Tensor):
        result = self.model(
            x,
            share_inputs=True,
            share_outputs=True,
        )
        return torch.from_numpy(result[0])


class OpenVinoTextDecoder(nn.Module):
    def __init__(self, model: str):
        super().__init__()

        self.core = Core()
        self._model = self.core.read_model(
            hf_hub_download(repo_id=f"zhuzilin/whisper-openvino-{model}", filename="decoder.xml"),
            hf_hub_download(repo_id=f"zhuzilin/whisper-openvino-{model}", filename="decoder.bin"),
        )
        self.model = self.core.compile_model(self._model, "CPU")

    def forward(self, x: Tensor, xa: Union[Tensor, np.ndarray], kv_cache: Tensor, offset: int):
        output = self.model(
            {
                "tokens": x,
                "audio_features": xa,
                "kv_cache": kv_cache,
                "offset": np.array(offset, dtype=int),
            },
            share_inputs=True,
            share_outputs=True,
        )
        return torch.from_numpy(output["logits"]), output["output_kv_cache"]


class Whisper(nn.Module):
    def __init__(self, dims: ModelDimensions, model: str):
        super().__init__()
        self.type = model
        self.dims = dims
        self.encoder = OpenVinoAudioEncoder(model=model)
        self.decoder = OpenVinoTextDecoder(model=model)

    def embed_audio(self, mel: torch.Tensor):
        return self.encoder.forward(mel)

    def logits(self, tokens: torch.Tensor, audio_features: Union[torch.Tensor, np.ndarray]):
        kv_cache = self.new_kv_cache(tokens.shape[0], tokens.shape[-1])
        output, _ = self.decoder.forward(tokens, audio_features, kv_cache=kv_cache, offset=0)
        return output

    def forward(self, mel: torch.Tensor, tokens: torch.Tensor) -> Dict[str, torch.Tensor]:
        kv_cache = self.new_kv_cache(tokens.shape[0], tokens.shape[-1])
        output, _ = self.decoder(tokens, self.encoder(mel), kv_cache=kv_cache, offset=0)
        return output

    @property
    def is_multilingual(self):
        return self.dims.n_vocab == 51865

    def new_kv_cache(self, n_group: int, length: int):
        if self.type == "tiny.en" or self.type == "tiny":
            size = [8, n_group, length, 384]
        elif self.type == "base.en" or self.type == "base":
            size = [12, n_group, length, 512]
        elif self.type == "small.en" or self.type == "small":
            size = [24, n_group, length, 768]
        elif self.type == "medium.en" or self.type == "medium":
            size = [48, n_group, length, 1024]
        elif self.type == "large":
            size = [64, n_group, length, 1280]
        else:
            raise ValueError(f"Unsupported model type: {self.type}")
        return np.zeros(size, dtype=np.float32)

    detect_language = detect_language_function
    transcribe = transcribe_function
    decode = decode_function
