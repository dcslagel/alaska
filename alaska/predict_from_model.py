# MIT License
# Copyright (c) 2018 Yimai Fang

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

# Code modified from Yimai Fang's seq2seq-summarizer
# repository: https://github.com/ymfa/seq2seq-summarizer

# Copyright (c) 2021 The AlasKA Developers.
# Distributed under the terms of the MIT License.
# SPDX-License_Identifier: MIT
"""
For evaluating the model and using it to make predictions on LAS mnemonics
"""
import tarfile
from typing import Tuple, List
import math
import torch
from .utils import Vocab, OOVDict, Batch, format_tokens, Dataset
from .model import DEVICE, Seq2SeqOutput, Seq2Seq
from .params import Params
from .get_data_path import get_data_path


def decode_batch_output(
    decoded_tokens, vocab: Vocab, oov_dict: OOVDict
) -> List[List[str]]:
    """Convert word indices to strings."""
    decoded_batch = []
    if not isinstance(decoded_tokens, list):
        decoded_tokens = decoded_tokens.transpose(0, 1).tolist()
    for i, doc in enumerate(decoded_tokens):
        decoded_doc = []
        for word_idx in doc:
            if word_idx >= len(vocab):
                word = oov_dict.index2word.get((i, word_idx), "<UNK>")
            else:
                word = vocab[word_idx]
            decoded_doc.append(word)
            if word_idx == vocab.EOS:
                break
        decoded_batch.append(decoded_doc)
    return decoded_batch


def eval_bs_batch(
    batch: Batch,
    model: Seq2Seq,
    vocab: Vocab,
    *,
    pack_seq=True,
    beam_size=4,
    min_out_len=1,
    max_out_len=None,
    len_in_words=True,
    best_only=True,
    details: bool = True
):
    """
    :param batch: a test batch of a single example
    :param model: a trained summarizer
    :param vocab: vocabulary of the trained summarizer
    :param pack_seq: currently has no effect as batch size is 1
    :param beam_size: the beam size
    :param min_out_len: required minimum output length
    :param max_out_len: required maximum output length (if None, use the model's own value)
    :param len_in_words: if True, count output length in words instead of tokens (i.e. do not count
                         punctuations)
    :param best_only: if True, run ROUGE only on the best hypothesis instead of all `beam size` many
    :param details: if True, also return a string containing the result of this document
    :return: mnemonics and predicted label

    Use a trained summarizer to predict
    """
    assert len(batch.examples) == 1
    with torch.no_grad():
        input_tensor = batch.input_tensor.to(DEVICE)
        hypotheses = model.beam_search(
            input_tensor,
            batch.input_lengths if pack_seq else None,
            batch.ext_vocab_size,
            beam_size,
            min_out_len=min_out_len,
            max_out_len=max_out_len,
            len_in_words=len_in_words,
        )
    if best_only:
        to_decode = [hypotheses[0].tokens]
        probability = math.log(-hypotheses[0].avg_log_prob, 10)
    else:
        to_decode = [h.tokens for h in hypotheses]
    decoded_batch = decode_batch_output(to_decode, vocab, batch.oov_dict)
    if details:
        # predicted = format_tokens(decoded_batch[0])
        predict_lst = format_tokens(decoded_batch[0]).split()
        predicted = str(predict_lst[0]) + " " + str(predict_lst[1])
    else:
        predicted = None
    if details:
        mnem = format_tokens(batch.examples[0].src).split()[-1]
    return predicted, mnem, probability


def eval_bs(test_set: Dataset, vocab: Vocab, model: Seq2Seq, params: Params):
    """
    :param test_set: dataset of summaries
    :param vocab: vocabularies of model
    :param model: model to use
    :param params: parameter file to read from
    :return: dictionary of predicted outputs
    Predict labels from summaries
    """
    test_gen = test_set.generator(1, vocab, None, True if params.pointer else False)
    n_samples = int(params.test_sample_ratio * len(test_set.pairs))
    save_path = str(get_data_path("results.tgz"))
    if params.test_save_results and params.model_path_prefix:
        result_file = tarfile.open(save_path, "w:gz")
    output, prob_output = {}, {}
    model.eval()
    for _ in range(1, n_samples + 1):
        batch = next(test_gen)
        predicted, mnem, prob = eval_bs_batch(
            batch,
            model,
            vocab,
            pack_seq=params.pack_seq,
            beam_size=params.beam_size,
            min_out_len=params.min_out_len,
            max_out_len=params.max_out_len,
            len_in_words=params.out_len_in_words,
            details=result_file is not None,
        )
        if predicted:
            output[mnem] = predicted
            prob_output[mnem] = prob
    return output, prob_output


def make_prediction(test_path):
    """
    :param test_path: path to LAS file
    :return: dictionary of mnemonic and label
    Make predictions using pointer generator
    """
    p = Params()
    dataset = Dataset(
        p.data_path,
        max_src_len=p.max_src_len,
        max_tgt_len=p.max_tgt_len,
        truncate_src=p.truncate_src,
        truncate_tgt=p.truncate_tgt,
    )
    v = dataset.build_vocab(p.vocab_size, embed_file=p.embed_file)
    m = Seq2Seq(v, p)
    state_dict_path = str(get_data_path("state_dict.pth"))
    m.load_state_dict(torch.load(state_dict_path))
    m.encoder.gru.flatten_parameters()
    m.decoder.gru.flatten_parameters()

    d = Dataset(test_path)
    output, prob_output = eval_bs(d, v, m, p)
    return output, prob_output
