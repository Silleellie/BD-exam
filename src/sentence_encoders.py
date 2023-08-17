from abc import ABC, abstractmethod
from math import ceil
from typing import Literal, List

import datasets
import torch
import numpy as np

from sentence_transformers import SentenceTransformer
from tqdm import tqdm
from transformers import BertModel, BertTokenizerFast


class SentenceEncoder(ABC):

    def __init__(self, batch_size: int = 64):
        self.batch_size = batch_size

    @abstractmethod
    def encode_batch(self, batch_sentences: List[str]):
        raise NotImplementedError

    def __call__(self, *sentences: str, desc: str = None) -> np.ndarray:
        outputs = []

        dataset = datasets.Dataset.from_dict({"sentences": sentences})

        pbar = tqdm(dataset.iter(batch_size=self.batch_size),
                    desc="Encoding labels for clustering..." if desc is None else desc,
                    total=ceil(dataset.num_rows / self.batch_size))

        for sample in pbar:
            batch_sentences = sample["sentences"]

            encoded_batch = self.encode_batch(batch_sentences)
            outputs.append(encoded_batch)

        pbar.close()

        return torch.vstack(outputs).numpy()


class SentenceTransformerEncoder(SentenceEncoder):

    def __init__(self, model_name='all-MiniLM-L6-v2', batch_size=128, model_kwargs=None):

        super().__init__(batch_size=batch_size)

        if model_kwargs is None:
            model_kwargs = {}

        self.model = SentenceTransformer(model_name, **model_kwargs)

    def encode_batch(self, batch_sentences: List[str]):
        return self.model.encode(batch_sentences, batch_size=self.batch_size)


class BertSentenceEncoder(SentenceEncoder):

    def __init__(self, model_name='bert-base-uncased',
                 batch_size=128,
                 hidden_states_num=4,
                 hidden_states_fusion_strat: Literal["sum", "concat"] = "sum",
                 token_fusion_strat: Literal["sum", "mean"] = "sum",
                 model_kwargs=None):

        super().__init__(batch_size=batch_size)

        if model_kwargs is None:
            model_kwargs = {}

        hidden_states_available_fusions = {"sum": self._fuse_hidden_states_sum,
                                           "concat": self._fuse_hidden_states_concat}

        token_available_fusions = {"sum": self._fuse_token_sum,
                                   "mean": self._fuse_token_mean}

        self.hidden_states_fusion_strat = hidden_states_available_fusions[hidden_states_fusion_strat]
        self.token_fusion_strat = token_available_fusions[token_fusion_strat]

        self.tokenizer = BertTokenizerFast.from_pretrained(model_name)
        self.model = BertModel.from_pretrained(model_name, output_hidden_states=True, **model_kwargs)
        self.hidden_states_num = hidden_states_num

    def _fuse_token_sum(self, *hidden_states: torch.Tensor):
        # this will remove the token dimension for each hidden state:
        # Input single hidden state: (batch_size x tokens x latent_dim) -> Output: (batch_size x latent_dim)
        return [torch.sum(single_hidden_state, dim=1) for single_hidden_state in hidden_states]

    def _fuse_token_mean(self, *hidden_states: torch.Tensor):
        # this will remove the token dimension for each hidden state:
        # Input single hidden state: (batch_size x tokens x latent_dim) -> Output: (batch_size x latent_dim)
        return [torch.mean(single_hidden_state, dim=1) for single_hidden_state in hidden_states]

    def _fuse_hidden_states_concat(self, list_hs_encoded_sentences: List[torch.Tensor]):
        # the token dim has been removed, so input is (batch_size x latent_dim)
        # input: (batch_size x latent_dim) -> (batch_size x (latent_dim * num_hidden_states))
        return torch.concat(list_hs_encoded_sentences, dim=1)

    def _fuse_hidden_states_sum(self, list_hs_encoded_sentences: List[torch.Tensor]):
        # the token dim has been removed, so input is (batch_size x latent_dim)
        # input: (batch_size x latent_dim) -> (batch_size x latent_dim)
        # elements are summed batch-wise over the hidden states extracted
        return torch.stack(list_hs_encoded_sentences).sum(dim=0)

    def encode_batch(self, batch_sentences: List[str]):
        tokenized_sentences = self.tokenizer(batch_sentences, return_tensors='pt', truncation=True, padding=True)

        with torch.no_grad():
            output_hidden_states = self.model(**tokenized_sentences).hidden_states[-self.hidden_states_num:]

        list_hs_encoded_sentences = self.token_fusion_strat(*output_hidden_states)

        hidden_states_fused = self.hidden_states_fusion_strat(list_hs_encoded_sentences)

        return hidden_states_fused
